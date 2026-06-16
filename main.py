import os
import re
import cv2
import easyocr
import whisper
import yt_dlp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

class VideoRequest(BaseModel):
    video_url: str

def parse_recipe_from_text(text_pool):
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
                
    if not ingredients and not directions:
        for line in lines:
            if re.search(r'^\d+(?:/\d+)?\s*(?:g|ml|oz|lbs?|cups?|tbsp|tsp|pinches)\b', line, re.IGNORECASE) or line.startswith('-'):
                ingredients.append(line.lstrip('- ').strip())
            elif re.match(r'^\d+[\.\)]', line) or any(act in line.lower() for act in ['mix', 'bake', 'fry', 'chop', 'add', 'pour']):
                directions.append(re.sub(r'^\d+[\.\)]\s*', '', line).strip())
                
    return ingredients, directions

@app.post("/extract")
def extract_recipe(payload: VideoRequest):
    video_file = "fallback_video.mp4"
    ydl_opts = {
        'format': 'worstvideo[ext=mp4]+worstaudio[ext=m4a]/worst[ext=mp4]',
        'outtmpl': video_file,
        'quiet': True
    }
    
    gathered_text_chunks = []
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([payload.video_url])
            
        # 1. Process Audio with Whisper
        try:
            audio_model = whisper.load_model("tiny")
            transcript = audio_model.transcribe(video_file)
            if transcript.get("text"):
                gathered_text_chunks.append(transcript["text"])
        except Exception as e:
            pass

        # 2. Process Video Frames with EasyOCR
        try:
            reader = easyocr.Reader(['en'], gpu=False)
            cap = cv2.VideoCapture(video_file)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30
            frame_count = 0
            
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_count % int(fps * 2) == 0:
                    ocr_results = reader.readtext(frame, detail=0)
                    if ocr_results:
                        gathered_text_chunks.append(" ".join(ocr_results))
                frame_count += 1
            cap.release()
        except Exception as e:
            pass
            
    except Exception as e:
        if os.path.exists(video_file): os.remove(video_file)
        raise HTTPException(status_code=500, detail=str(e))
        
    if os.path.exists(video_file): 
        os.remove(video_file)
        
    full_text_pool = "\n".join(gathered_text_chunks)
    ingredients, directions = parse_recipe_from_text(full_text_pool)
    
    return {
        "ingredients": ingredients,
        "directions": directions,
        "raw_dump": full_text_pool[:2000] # truncate safely
    }