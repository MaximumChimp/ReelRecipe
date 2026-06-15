import os
import re
import cv2
import easyocr
import whisper
import yt_dlp
from google import genai
from django.shortcuts import render, redirect
from django.utils import timezone

def get_video_data_free(video_url):
    """
    Tier 2 Helper: Fetches core metadata using yt-dlp.
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
        return "Culinary Extraction", "10 Mins", "", f"Meta fallback context. Target URL: {video_url}"


def parse_recipe_from_text(title, text_pool):
    """
    A smart text regex engine that filters ingredients and steps out of any string pool
    (Whether compiled from creator descriptions, Whisper transcripts, or EasyOCR frames).
    """
    ingredients = []
    directions = []
    
    lines = [line.strip() for line in text_pool.split('\n') if line.strip()]
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
                
    # Regex fallback if text is flat/unstructured
    if not ingredients and not directions:
        for line in lines:
            if re.search(r'^\d+(?:/\d+)?\s*(?:g|ml|oz|lbs?|cups?|tbsp|tsp|pinches)\b', line, re.IGNORECASE) or line.startswith('-'):
                ingredients.append(line.lstrip('- ').strip())
            elif re.match(r'^\d+[\.\)]', line) or any(act in line.lower() for act in ['mix', 'bake', 'fry', 'chop', 'add', 'pour']):
                directions.append(re.sub(r'^\d+[\.\)]\s*', '', line).strip())
                
    return ingredients, directions


def run_deep_multimedia_fallback(video_url):
    """
    Tier 3 Ultimate Fallback: Downloads video, uses Whisper for spoken words,
    and runs EasyOCR on raw video frames to catch on-screen ingredients text.
    """
    video_file = "/tmp/fallback_video.mp4"
    
    # Download lower quality video stream quickly to save disk bandwidth
    ydl_opts = {
        'format': 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]',
        'outtmpl': video_file,
        'quiet': True
    }
    
    gathered_text_chunks = []
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            
        # 1. Spoken Audio Processing via Whisper
        try:
            audio_model = whisper.load_model("tiny") # Tiny model is fast and uses minimal RAM
            transcript = audio_model.transcribe(video_file)
            if transcript.get("text"):
                gathered_text_chunks.append("--- Spoken Transcript ---")
                gathered_text_chunks.append(transcript["text"])
        except Exception as e:
            gathered_text_chunks.append(f"[Audio Error: {str(e)}]")

        # 2. On-Screen Text Tracking via OpenCV + EasyOCR
        try:
            reader = easyocr.Reader(['en'], gpu=False)
            cap = cv2.VideoCapture(video_file)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            
            frame_count = 0
            gathered_text_chunks.append("\n--- Text Observed On Screen ---")
            
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Sample 1 frame every 2 seconds to keep processing fast
                if frame_count % int(fps * 2) == 0:
                    ocr_results = reader.readtext(frame, detail=0)
                    if ocr_results:
                        line_text = " ".join(ocr_results).strip()
                        if len(line_text) > 3:
                            gathered_text_chunks.append(line_text)
                            
                frame_count += 1
            cap.release()
        except Exception as e:
            gathered_text_chunks.append(f"[OCR Vision Error: {str(e)}]")
            
    except Exception as download_err:
        return f"Video stream could not be pulled: {str(download_err)}"
        
    # Clean up downloaded file from local storage container
    if os.path.exists(video_file):
        os.remove(video_file)
        
    return "\n".join(gathered_text_chunks)


def home_view(request):
    context = {'now': timezone.now()}
    
    if request.method == 'POST':
        video_url = request.POST.get('video_url')
        
        if video_url:
            # Gather base details
            title, duration, description, raw_context = get_video_data_free(video_url)
            
            prompt = f"""
                You are an expert culinary data extractor. Analyze the following video metadata:
                {raw_context}

                If directions and ingredients are sparse, use your knowledge of the recipe title ("{title}") to reconstruct it. 

                Provide the output strictly in this exact format:
                INGREDIENTS:
                - [Amount] [Ingredient Name]

                DIRECTIONS:
                - [Step description]
                """
            
            try:
                # --- TIER 1: Try Gemini API ---
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
                    directions = ["Structure anomaly. Read trace log below."]
                
                raw_log_payload = ai_output

            except Exception as gemini_err:
                # --- TIER 2: Gemini fails, fall back to parsing Creator Text ---
                print(f"Gemini failed: {str(gemini_err)}. Launching local failover engine...")
                
                ingredients, directions = parse_recipe_from_text(title, description)
                log_source = "Parsed from Creator Video Description Data"
                
                # --- TIER 3: Description is blank! Fall back to Whisper + EasyOCR ---
                if len(ingredients) <= 1 and (not description or len(description.strip()) < 10):
                    print("Description text empty. Initializing computer vision and speech pipelines...")
                    multimedia_dump = run_deep_multimedia_fallback(video_url)
                    
                    # Parse whatever text was picked up via video scanning and voice audio extraction
                    ingredients, directions = parse_recipe_from_text(title, multimedia_dump)
                    log_source = f"Generated via Local AI Frame Scanning & Audio Transcription:\n\n{multimedia_dump}"
                
                # Ultimate catch-all display values if text pools yielded no structured steps
                if not ingredients or "Could not isolate" in str(ingredients):
                    ingredients = ["No text found. Watch video visuals for exact measurements."]
                if not directions:
                    directions = [f"Follow along with the original timeline components to cook your {title}."]
                
                raw_log_payload = (
                    f"[SYSTEM NOTICE: Cloud AI Offline - Local Failover Pipeline Activated]\n"
                    f"Gemini Exception Trace: {str(gemini_err)}\n\n"
                    f"--- Strategy Log ---\n{log_source}"
                )
            
            # Pack values safely to display on dashboard card
            request.session['cached_recipe'] = {
                'recipe_title': title,
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

# Keep standard compliance views active below
def privacy_policy(request): return render(request, 'includes/privacy_policy.html', {'now': timezone.now()})
def terms_of_service(request): return render(request, 'includes/terms_of_service.html', {'now': timezone.now()})