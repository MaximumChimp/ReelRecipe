import os
import re
import requests
import yt_dlp
from google import genai
from django.shortcuts import render, redirect
from django.utils import timezone

# 🚨 Live API Worker Link Configuration
HF_SPACE_API_URL = "https://maximumchimp-reel-recipe-ai-worker.hf.space/extract"


def get_video_data_free(video_url):
    """
    Tier 2 Helper: Fetches core metadata and description text using yt-dlp.
    """
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'writeautomaticsub': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            title = info.get('title', 'Extracted Recipe')
            duration = f"{int(info.get('duration', 0) / 60)} Mins" if info.get('duration') else "10 Mins"
            description = info.get('description', '')
            context_payload = f"Video Title: {title}\nDescription text: {description}\n"
            return title, duration, description, context_payload
    except Exception:
        return "Culinary Extraction", "10 Mins", "", "Meta fallback context. Target URL: " + str(video_url)


def parse_recipe_from_text_fallback(title, description):
    """
    Local Regex Fallback: If both Gemini and Hugging Face space fail, 
    this salvages ingredients and steps from the raw description string.
    """
    ingredients = []
    directions = []
    
    lines = [line.strip() for line in description.split('\n') if line.strip()]
    current_section = None
    
    for line in lines:
        lower_line = line.lower()
        if any(kw in lower_line for kw in ['ingredient', 'components', 'what you need', 'shopping list']):
            current_section = 'ingredients'
            continue
        elif any(kw in lower_line for kw in ['direction', 'instruction', 'method', 'step', 'how to make']):
            current_section = 'directions'
            continue
            
        if current_section == 'ingredients' or line.startswith('-') or line.startswith('•'):
            clean_ing = line.lstrip('-• ').strip()
            if clean_ing and len(clean_ing) < 100:
                ingredients.append(clean_ing)
        elif current_section == 'directions' or re.match(r'^\d+[\.\)]', line):
            clean_dir = re.sub(r'^\d+[\.\)]\s*', '', line).strip()
            if clean_dir:
                directions.append(clean_dir)
                
    if not ingredients and not directions:
        for line in lines:
            if re.search(r'^\d+(?:/\d+)?\s*(?:g|ml|oz|lbs?|cups?|tbsp|tsp|pinches)\b', line, re.IGNORECASE) or line.startswith('-'):
                ingredients.append(line.lstrip('- ').strip())
            elif re.match(r'^\d+[\.\)]', line) or any(act in line.lower() for act in ['mix', 'bake', 'fry', 'chop', 'add', 'pour']):
                directions.append(re.sub(r'^\d+[\.\)]\s*', '', line).strip())
                
    if not ingredients or "Could not isolate" in str(ingredients):
        ingredients = ["No explicit text format ingredients found. Use video player controls to review recipe components visually."]
    if not directions:
        directions = [f"Follow along with the original media timeline metrics to process your {title} setup."]
        
    return ingredients, directions


def home_view(request):
    context = {'now': timezone.now()}
    
    if request.method == 'POST':
        video_url = request.POST.get('video_url')
        
        if video_url:
            # Unpack the video description context
            title, duration, description, raw_context = get_video_data_free(video_url)
            
            try:
                # 🛑 GUARDRAIL CHECK: If metadata failed, bypass Gemini immediately to prevent hallucinating computational data
                if title == "Culinary Extraction" or "Meta fallback context" in raw_context:
                    raise Exception("Scraper meta block triggered: Title returned fallback string placeholders.")

                prompt = f"""
                    You are an expert culinary data extractor. Analyze the following video metadata:
                    {raw_context}

                    CRITICAL: If directions and ingredients are sparse, use your deep culinary knowledge of the recipe title ("{title}") to reconstruct it accurately.
                    Do NOT invent technical, code, or data-extraction steps.

                    Provide the output strictly in this exact format:
                    INGREDIENTS:
                    - [Amount] [Ingredient Name]

                    DIRECTIONS:
                    - [Step description]
                    """
                
                # --- TIER 1: Standard Execution via Cloud Gemini ---
                client = genai.Client()
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                )
                ai_output = response.text
                
                if "INGREDIENTS:" in ai_output and "DIRECTIONS:" in ai_output:
                    parts = ai_output.split("DIRECTIONS:")
                    ing_block = parts[0].replace("INGREDIENTS:", "").strip()
                    dir_block = parts[1].strip()
                    
                    ingredients = [line.strip("- ").strip() for line in ing_block.split("\n") if line.strip()]
                    directions = [line.strip().lstrip('0123456789.-• ') for line in dir_block.split("\n") if line.strip()]
                else:
                    ingredients = [line.strip() for line in ai_output.split("\n") if line.strip()][:5]
                    directions = ["Structure layout anomaly detected. Parsing error logs for source details."]
                
                raw_log_payload = ai_output

            except Exception as gemini_err:
                # --- TIER 2 & 3: Gemini Failure / Meta Block Corrupted / Quota Exceeded ---
                print(f"Bypassing/Failing Gemini processing branch due to: {str(gemini_err)}")
                print("Forwarding structural analysis directly to Hugging Face worker instance...")
                
                try:
                    # POST the processing job to your remote 16GB RAM Hugging Face container instance
                    hf_response = requests.post(
                        HF_SPACE_API_URL,
                        json={"video_url": video_url},
                        timeout=50  # Gives Whisper/EasyOCR plenty of time to run multimedia audio/video pipelines
                    )
                    
                    if hf_response.status_code == 200:
                        data = hf_response.json()
                        
                        # Extract the true title parsed by Hugging Face!
                        if data.get("title") and data.get("title") != "Media Video Recipe":
                            title = data.get("title")
                            
                        ingredients = data.get("ingredients", [])
                        directions = data.get("directions", [])
                        
                        if not ingredients:
                            ingredients = ["No explicit text format ingredients found. Use video player controls to review recipe components visually."]
                        if not directions:
                            directions = [f"Follow along with the original media timeline metrics to process your recipe setup."]
                            
                        raw_log_payload = (
                            f"[SYSTEM NOTICE: Cloud AI Offline - Hugging Face Fallback Active]\n"
                            f"Execution Engine Context: {str(gemini_err)}\n\n"
                            f"--- HF Video Scan Log ---\n{data.get('raw_dump', 'No video transcription logs returned.')}"
                        )
                    else:
                        raise Exception(f"Hugging Face worker returned a bad response status code: {hf_response.status_code}")
                        
                except Exception as hf_err:
                    # --- EMERGENCY TIER 4: Absolute Catch-All via Local Regex Engine ---
                    print(f"Hugging Face worker unavailable ({str(hf_err)}). Defaulting to local description scrape...")
                    ingredients, directions = parse_recipe_from_text_fallback(title, description)
                    
                    raw_log_payload = (
                        f"[SYSTEM NOTICE: Severe Multi-Engine Failure - Processing Local Text Only]\n"
                        f"Primary Engine Log: {str(gemini_err)}\n"
                        f"Secondary Engine Log: {str(hf_err)}\n\n"
                        f"--- Raw Creator Text Description ---\n"
                        f"{description if description else 'No raw text metadata strings available from target creator link.'}"
                    )
            
            # Pack values safely to display on your template card components
            request.session['cached_recipe'] = {
                'recipe_title': title if title != "Culinary Extraction" else "Media Recipe Link",
                'recipe_duration': duration,
                'recipe_ingredients': ingredients,
                'recipe_directions': directions,
                'raw_log': raw_log_payload
            }
            return redirect('/')
            
    if 'cached_recipe' in request.session:
        recipe_data = request.session.pop('cached_recipe')
        context.update(recipe_data)
        context['recipe_extracted'] = True

    return render(request, 'core/home.html', context)


def privacy_policy(request):
    return render(request, 'includes/privacy_policy.html', {'now': timezone.now()})


def terms_of_service(request):
    return render(request, 'includes/terms_of_service.html', {'now': timezone.now()})