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
            # Gather base details (Title, Description, etc.)
            title, duration, description, raw_context = get_video_data_free(video_url)
            
            # Count how many words are actually in the creator's description box
            description_word_count = len(description.strip().split()) if description else 0
            
            try:
                # 🛑 CRITICAL INTERCEPT: If the metadata is empty, corrupted, or has fewer than 8 words,
                # do NOT send it to Gemini. Force an immediate skip to the Hugging Face multimedia engine!
                if title == "Culinary Extraction" or description_word_count < 8:
                    raise Exception("Sparse metadata guardrail triggered. Bypassing Gemini to run deep video/audio scan...")

                prompt = f"""
                    You are an expert culinary data extractor. Analyze the following video metadata:
                    {raw_context}

                    Clean up, extract, and accurately structure the ingredient metrics and cooking actions found in the text.
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
                    raise Exception("Gemini layout output did not match required structural blocks.")
                
                raw_log_payload = ai_output

            except Exception as gemini_err:
                # --- TIER 2 & 3: Gemini Failure OR Sparse Metadata Guardrail Triggered ---
                print(f"Skipping Gemini branch: {str(gemini_err)}")
                print("Forwarding job to remote Hugging Face video parsing cluster...")
                
                try:
                    # POST the processing job to your remote 16GB RAM Hugging Face container instance
                    hf_response = requests.post(
                        HF_SPACE_API_URL,
                        json={"video_url": video_url},
                        timeout=60  # Generous timeout for deep frame-by-frame and speech parsing
                    )
                    
                    if hf_response.status_code == 200:
                        data = hf_response.json()
                        
                        # Sync up the true video title caught by Hugging Face's downloader
                        if data.get("title") and data.get("title") != "Media Video Recipe":
                            title = data.get("title")
                            
                        ingredients = data.get("ingredients", [])
                        directions = data.get("directions", [])
                        
                        # Ensure we don't display empty cards if the models missed the text cues
                        if not ingredients:
                            ingredients = ["No explicit text ingredients observed on screen. Review video timeline components visually."]
                        if not directions:
                            directions = ["No concrete structural audio cues found. Follow the video visuals for steps."]
                            
                        raw_log_payload = (
                            f"[SYSTEM NOTICE: Cloud AI Bypassed - Hugging Face Multimedia Pipeline Active]\n"
                            f"Routing Context: {str(gemini_err)}\n\n"
                            f"--- HF Video Scan Log Map ---\n{data.get('raw_dump', 'No media string matrix data returned.')}"
                        )
                    else:
                        raise Exception(f"Hugging Face worker container returned an unstable code: {hf_response.status_code}")
                        
                except Exception as hf_err:
                    # --- EMERGENCY TIER 4: Ultimate Text Fallback Engine ---
                    print(f"Hugging Face worker down or timed out ({str(hf_err)}). Defaulting to regex description scrape...")
                    ingredients, directions = parse_recipe_from_text_fallback(title, description)
                    
                    raw_log_payload = (
                        f"[SYSTEM NOTICE: Severe Operational Failover - Processing Fallback Text Variables Only]\n"
                        f"Tier 1 Exception: {str(gemini_err)}\n"
                        f"Tier 2 Exception: {str(hf_err)}\n\n"
                        f"--- Raw Text Metadata description Buffer ---\n"
                        f"{description if description else 'No raw metadata script description available for this target streaming URL.'}"
                    )
            
            # Pack values safely to display on your HTML template dashboard cards
            request.session['cached_recipe'] = {
                'recipe_title': title if title != "Culinary Extraction" else "Video Cooking Recipe",
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