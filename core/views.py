import os
import json
import re
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError 
from django.shortcuts import render, redirect
from django.utils import timezone
from pydantic import BaseModel, Field
from youtube_transcript_api import YouTubeTranscriptApi

# --- Pydantic Data Schemas ---

class RecipeComponent(BaseModel):
    component_name: str = Field(description="The category stage like 'For Sauce', 'For Meat', etc.")
    items: list[str] = Field(description="List of ingredients or step descriptions for this specific stage")

class StructuredRecipe(BaseModel):
    recipe_title: str
    recipe_duration: str
    recipe_ingredients: list[RecipeComponent]
    recipe_directions: list[RecipeComponent]

# --- Helper Functions ---

def extract_youtube_id(url):
    """
    Extracts the 11-character YouTube video ID from various URL string formats.
    Matches standard, shortened, embed, and share link variations.
    """
    pattern = r'(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|^([^#\&\?]{11}))([^#\&\?]{11})'
    match = re.search(pattern, url)
    if match:
        return match.group(2) if match.group(2) else match.group(1)
    return None

def fallback_parser_without_ai(url, error_message=None):
    """
    If Gemini or the transcript API fails, this returns a safe structural response
    to keep the user interface stable.
    """
    msg = error_message or "Gemini servers are currently busy. Please try clicking submit again."
    ingredients = [
        "For \"System Notification\"",
        f"    - {msg}"
    ]
    directions = [
        "For \"Processing Stage\"",
        "    - Extraction paused. Please verify your video link contains captions/transcript options."
    ]
    return "Recipe Extraction Failed", "0 Mins", ingredients, directions

# --- Django Views ---

def home_view(request):
    context = {'now': timezone.now()}
    
    if request.method == 'POST':
        video_url = request.POST.get('video_url')
        
        if video_url:
            client = genai.Client()
            video_id = extract_youtube_id(video_url)
            transcript_text = ""
            
            # --- Phase 1: Try gathering YouTube Text Data ---
            if video_id:
                try:
                    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                    transcript_text = " ".join([item['text'] for item in transcript_list])
                except Exception as transcript_err:
                    print(f"Transcript Error: {str(transcript_err)}")
                    # If no captions exist, we explicitly inform the model or drop back to URL strings
                    transcript_text = ""
            
            # If completely invalid URL form or extraction couldn't parse an ID
            if not video_id:
                title, duration, ingredients, directions = fallback_parser_without_ai(
                    video_url, "Invalid YouTube link structure provided."
                )
                ai_response = None
            else:
                # Configuration settings for retrying API requests
                max_retries = 3
                retry_delay = 2 
                ai_response = None

                # --- Phase 2: Querying Gemini 2.5 Flash ---
                for attempt in range(max_retries):
                    try:
                        prompt = f"""
                        You are an expert culinary AI specializing in transcribing recipe tutorials into clean documentation.
                        Your task is to analyze the provided YouTube data segment and extract an exceptionally accurate, highly structured recipe.

                        ### YOUTUBE DATA:
                        - Video Target URL: {video_url}
                        - Visual Transcript Data: {transcript_text if transcript_text else "No transcript tracks detected. Rely on contextual knowledge of this specific link."}

                        ### COMPONENT CATEGORIZATION GUIDELINES:
                        You MUST split both the ingredients block and instructions/directions list into clear categorical components or preparation stages of the dish (e.g., "For Sauce", "For Marination", "For Meat assembly"). Do not dump everything into a single generic bucket.
                        """

                        print(f"Processing through Gemini (Attempt {attempt + 1}/{max_retries}): {video_url}")
                        
                        ai_response = client.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                response_mime_type="application/json",
                                response_schema=StructuredRecipe,
                                temperature=0.1 # Dropped to 0.1 to maximize consistency and reduce hallucinated ingredients
                            )
                        )
                        break

                    except APIError as api_err:
                        if api_err.code == 503 and attempt < max_retries - 1:
                            print(f"Gemini 503 Busy. Waiting {retry_delay}s and retrying...")
                            time.sleep(retry_delay)
                            retry_delay *= 2 
                            continue
                        else:
                            raise api_err 
                    except Exception as e:
                        raise e 

            # --- Phase 3: Parsing Response Objects ---
            try:
                if ai_response:
                    recipe_data = json.loads(ai_response.text)
                    
                    formatted_ingredients = []
                    for comp in recipe_data.get('recipe_ingredients', []):
                        formatted_ingredients.append(f"For \"{comp['component_name']}\"")
                        for item in comp.get('items', []):
                            formatted_ingredients.append(f"    - {item}")
                        formatted_ingredients.append("")

                    formatted_directions = []
                    for comp in recipe_data.get('recipe_directions', []):
                        formatted_directions.append(f"For \"{comp['component_name']}\"")
                        for item in comp.get('items', []):
                            formatted_directions.append(f"    - {item}")
                        formatted_directions.append("")

                    title = recipe_data.get('recipe_title', 'Gemini Extracted Recipe')
                    duration = recipe_data.get('recipe_duration', 'Calculated from Video')
                    ingredients = [line for line in formatted_ingredients if line.strip or line == ""]
                    directions = [line for line in formatted_directions if line.strip or line == ""]
                    raw_log_payload = ai_response.text
                elif not video_id:
                    # Already generated fallback data during error detection
                    raw_log_payload = "Execution skipped: Invalid Video URL string patterns."
                else:
                    raise Exception("No response gathered from the AI cluster.")

            except Exception as e:
                print(f"Gemini Processing Exception: {str(e)}")
                title, duration, ingredients, directions = fallback_parser_without_ai(video_url, str(e))
                raw_log_payload = f"Error Tracking Capture: {str(e)}"

            # --- Phase 4: Stashing Session Payloads ---
            recipe_payload = {
                'recipe_title': recipe_data.get('recipe_title', 'Gemini Extracted Recipe'),
                'recipe_duration': recipe_data.get('recipe_duration', 'Calculated from Video'),
                'recipe_ingredients': recipe_data.get('recipe_ingredients', []),  # Pass list of objects directly
                'recipe_directions': recipe_data.get('recipe_directions', [])     # Pass list of objects directly
            }
            
            request.session['cached_recipe'] = {
                'recipe_json': json.dumps(recipe_payload, indent=4),
                'raw_log': raw_log_payload
            }
            return redirect('/')
            
    if 'cached_recipe' in request.session:
        recipe_data = request.session.pop('cached_recipe')
        parsed_recipe = json.loads(recipe_data['recipe_json'])
        context.update(parsed_recipe)
        context['raw_log'] = recipe_data['raw_log']
        context['recipe_json_string'] = recipe_data['recipe_json']
        context['recipe_extracted'] = True

    return render(request, 'core/home.html', context)


def privacy_policy(request):
    return render(request, 'includes/privacy_policy.html', {'now': timezone.now()})


def terms_of_service(request):
    return render(request, 'includes/terms_of_service.html', {'now': timezone.now()})