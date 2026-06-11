import os
import re
import yt_dlp
from google import genai
from django.shortcuts import render, redirect  # Ensure redirect is here
from django.utils import timezone

def get_video_data_free(video_url):
    """
    Scrapes basic video metadata and any available text descriptions/transcripts 
    using yt-dlp to feed as clean context into Gemini.
    """
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'writeautomaticsub': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            
            # Extract core details
            title = info.get('title', 'Extracted Recipe')
            duration = f"{int(info.get('duration', 0) / 60)} Mins" if info.get('duration') else "10 Mins"
            description = info.get('description', '')
            
            # Build a strong contextual text block for Gemini to read
            context_payload = f"Video Title: {title}\nDescription text provided by creator: {description}\n"
            return title, duration, context_payload
            
    except Exception as e:
        return "Culinary Extraction", "10 Mins", f"Meta fallback context. Target URL: {video_url}"


def home_view(request):
    context = {'now': timezone.now()}
    
    if request.method == 'POST':
        video_url = request.POST.get('video_url')
        
        if video_url:
            # Python can read this now because it was defined above!
            title, duration, raw_context = get_video_data_free(video_url)
            
            prompt = f"""
                You are an expert culinary data extractor. Analyze the following video metadata:
                {raw_context}

                If the cooking directions and ingredient amounts are missing or sparse, use your extensive culinary knowledge of the recipe title ("{title}") to reconstruct a complete, highly accurate authentic recipe. 

                Provide the output strictly in this exact format:
                INGREDIENTS:
                - [Amount] [Ingredient Name]

                DIRECTIONS:
                1. [Step 1 description]
                """
            
            try:
                client = genai.Client()
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                )
                
                ai_output = response.text
                
                ingredients = []
                directions = []
                
                if "INGREDIENTS:" in ai_output and "DIRECTIONS:" in ai_output:
                    parts = ai_output.split("DIRECTIONS:")
                    ing_block = parts[0].replace("INGREDIENTS:", "").strip()
                    dir_block = parts[1].strip()
                    
                    ingredients = [line.strip("- ").strip() for line in ing_block.split("\n") if line.strip()]
                    directions = [line.strip().lstrip('0123456789. ') for line in dir_block.split("\n") if line.strip()]
                else:
                    ingredients = [line.strip() for line in ai_output.split("\n") if line.strip()][:5]
                
                # Save data to session cache
                request.session['cached_recipe'] = {
                    'recipe_title': title,
                    'recipe_duration': duration,
                    'recipe_ingredients': ingredients,
                    'recipe_directions': directions,
                    'raw_log': ai_output
                }
                
            except Exception as gemini_err:
                request.session['cached_recipe'] = {
                    'recipe_title': "API Configuration Notice",
                    'recipe_duration': "0 Mins",
                    'recipe_ingredients': ["Please verify your Gemini API key is valid."],
                    'recipe_directions': ["Check the trace payload below."],
                    'raw_log': f"Gemini Engine Error: {str(gemini_err)}"
                }
            
            # Redirect to drop the POST payload and protect your daily quota
            return redirect('/')
            
    # Process the safe GET display
    if 'cached_recipe' in request.session:
        recipe_data = request.session.pop('cached_recipe')
        context.update(recipe_data)
        context['recipe_extracted'] = True

    return render(request, 'core/home.html', context)
    

def privacy_policy(request):
    return render(request, 'includes/privacy_policy.html', {'now': timezone.now()})


def terms_of_service(request):
    return render(request, 'includes/terms_of_service.html', {'now': timezone.now()})