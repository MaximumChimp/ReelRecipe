import os
import re
import json  # Added for JSON serialization
import requests
import yt_dlp
from google import genai
from google.api_core.exceptions import GoogleAPIError
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
            title, duration, description, raw_context = get_video_data_free(video_url)
            description_word_count = len(description.strip().split()) if description else 0
            
            try:
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

            except (GoogleAPIError, Exception) as gemini_err:
                print(f"Gemini error or bypass caught: {str(gemini_err)}")
                print("Forwarding job to remote Hugging Face video parsing cluster...")
                
                try:
                    hf_response = requests.post(
                        HF_SPACE_API_URL,
                        json={"video_url": video_url},
                        timeout=60
                    )
                    
                    if hf_response.status_code == 200:
                        data = hf_response.json()
                        
                        if data.get("title") and data.get("title") != "Media Video Recipe":
                            title = data.get("title")
                            
                        ingredients = data.get("ingredients", [])
                        directions = data.get("directions", [])
                        
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
                    print(f"Hugging Face worker down or timed out ({str(hf_err)}). Defaulting to regex description scrape...")
                    ingredients, directions = parse_recipe_from_text_fallback(title, description)
                    
                    raw_log_payload = (
                        f"[SYSTEM NOTICE: Severe Operational Failover - Processing Fallback Text Variables Only]\n"
                        f"Tier 1 Exception: {str(gemini_err)}\n"
                        f"Tier 2 Exception: {str(hf_err)}\n\n"
                        f"--- Raw Text Metadata description Buffer ---\n"
                        f"{description if description else 'No raw metadata script description available for this target streaming URL.'}"
                    )
            
            # --- Structuring Data into JSON Format ---
            recipe_payload = {
                'recipe_title': title if title != "Culinary Extraction" else "Video Cooking Recipe",
                'recipe_duration': duration,
                'recipe_ingredients': ingredients,
                'recipe_directions': directions
            }
            
            # Save the clean recipe data as a JSON string, keeping the log payload separate for debugging
            request.session['cached_recipe'] = {
                'recipe_json': json.dumps(recipe_payload, indent=4),
                'raw_log': raw_log_payload
            }
            return redirect('/')
            
    if 'cached_recipe' in request.session:
        recipe_data = request.session.pop('cached_recipe')
        
        # Parse the JSON string back into native dictionary items so the HTML template continues working out-of-the-box
        parsed_recipe = json.loads(recipe_data['recipe_json'])
        context.update(parsed_recipe)
        
        context['raw_log'] = recipe_data['raw_log']
        context['recipe_json_string'] = recipe_data['recipe_json']  # Available if you want to output raw JSON on your dashboard
        context['recipe_extracted'] = True

    return render(request, 'core/home.html', context)


def privacy_policy(request):
    return render(request, 'includes/privacy_policy.html', {'now': timezone.now()})


def terms_of_service(request):
    return render(request, 'includes/terms_of_service.html', {'now': timezone.now()})