# gpu.py

import os
import ollama 
import re
import json
import pandas as pd
from datetime import datetime
import requests, time
import pynvml
from PyLibreHardwareMonitor import Computer


# Initialize the computer sensor manager once at startup
try:
    hardware_monitor = Computer()
    # Enable the sensors we care about
    hardware_monitor.motherboard = True
    hardware_monitor.gpu = True
    hardware_monitor.controller = True 
    HAS_HARDWARE_MONITOR = True
except Exception:
    HAS_HARDWARE_MONITOR = False

# Initialize NVIDIA Management Library
try:
    pynvml.nvmlInit()
    gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    HAS_GPU_TRACKING = True
except Exception as e:
    print(f"⚠️ NVML Initialization Failed: {e}. Running without deep telemetry.")
    HAS_GPU_TRACKING = False

def get_gpu_telemetry():
    """Queries raw silicon telemetry directly from the NVIDIA kernel driver."""
    if not HAS_GPU_TRACKING:
        return {"vram_gb": 0.0, "temp_c": 0, "clock_mhz": 0, "fan_pct": -1}
    
    mem = pynvml.nvmlDeviceGetMemoryInfo(gpu_handle)
    temp = pynvml.nvmlDeviceGetTemperature(gpu_handle, pynvml.NVML_TEMPERATURE_GPU)
    clock = pynvml.nvmlDeviceGetClockInfo(gpu_handle, pynvml.NVML_CLOCK_GRAPHICS)
    
    fan = -1
    
    # Direct hardware monitor fallback for fan telemetry
    if HAS_HARDWARE_MONITOR:
        try:
            detected_fans = []
            
            # Check motherboard and case fans managed by system controllers
            if hardware_monitor.motherboard:
                for hardware in hardware_monitor.motherboard:
                    for sensor in hardware.sensors:
                        if "fan" in sensor.name.lower() and sensor.value:
                            detected_fans.append(sensor.value)
                            
            # Check standalone controller chips or dedicated GPU fans
            if hardware_monitor.controller:
                for hardware in hardware_monitor.controller:
                    for sensor in hardware.sensors:
                        if "fan" in sensor.name.lower() and sensor.value:
                            detected_fans.append(sensor.value)

            # Take the highest active fan speed detected across the hardware layout
            if detected_fans:
                fan = int(max(detected_fans))
        except Exception:
            fan = -1
        
    return {
        "vram_gb": mem.used / (1024**3),
        "temp_c": temp,
        "clock_mhz": clock,
        "fan_pct": fan
    }

def check_active_hardware():
    """Queries the local Ollama daemon to see what hardware is processing the model."""
    try:
        response = requests.get("http://localhost:11434/api/ps")
        if response.status_code == 200:
            models = response.json().get("models", [])
            if not models:
                print("ℹ️ No models actively loaded in memory right now.")
                return
            for model in models:
                name = model.get("name")
                processor = model.get("processor", "Unknown")
                vram = model.get("size_vram", 0) / (1024**3) # Convert bytes to GB
                print(f"📊 Active Hardware Status -> Model: {name} | Backend Engine: {processor} | VRAM Used: {vram:.2f} GB")
        else:
            print("⚠️ Could not reach Ollama status endpoint.")
    except Exception as e:
        print(f"⚠️ Hardware check skipped: {e}")

# ==========================================
# EXTRACTION BLOCK SWAPPED TO LOCAL CHIP
# ==========================================
def fast_extract_table(image_path, params, json_schema):
    if not os.path.exists(image_path):
        print(f"Error: Image not found at {image_path}")
        return None, None
        
    prompt = f"""
    Analyze the image.
       
    Otherwise
    Extract the table rows and the bottom-most text timestamp line from the image exactly as they appear.
    You must output a structured JSON object with exactly these two keys:
    1. "table_data": An array of objects where each object contains:
       "Visa Location", "Visa Type", "Earliest Date", "Slots on Earliest Date", "Total Dates Available", "Last Seen At", "Relative Time"
    2. "footer_timestamp": The text string of the generation time row at the bottom.
    
    Rules:
    - Do not stop until the JSON is fully complete
    - Do not truncate any field
    - Always close all brackets and braces
    - Output must be valid parsable JSON
    - No partial objects allowed

    Return ONLY valid raw JSON data. Do not use markdown syntax block tags (```json).

    Required JSON Schema:{json_schema}

    """
    
    try:
        print("🔮 Launching high-speed VRAM extraction matrix via qwen2.5vl:3b...")
        # 📊 CAPTURE BASELINE HARDWARE METRICS BEFORE GENERATION
        base_metrics = get_gpu_telemetry()
        print(f"🔹 Baseline GPU State -> VRAM: {base_metrics['vram_gb']:.2f} GB | Temp: {base_metrics['temp_c']}°C | Fan: {base_metrics['fan_pct']}%")
        start_time = time.time()

        response = ollama.generate(
            model=params.get("model", "qwen2.5vl:3b"),
            prompt=prompt,
            format="json",
            images=[image_path],
            options={
                        "temperature": params.get("temperature"),
                        "num_predict": params.get("num_predict"),
                        "top_k": params.get("top_k"),
                        "top_p": params.get("top_p"),
                        "num_ctx": params.get("num_ctx"),
                        "num_gpu": -1,
                        "f16_kv": params.get("f16_kv"),
                        "stop": ["```"]
                    }
                )
        
        # 📊 CAPTURE PEAK METRICS IMMEDIATELY AFTER GENERATION
        generation_duration = time.time() - start_time
        peak_metrics = get_gpu_telemetry()
        
        # Calculate Δ VRAM = Peak VRAM - Base VRAM
        delta_vram = peak_metrics['vram_gb'] - base_metrics['vram_gb']
        
        print("\n📈 --- LIVE INFRASTRUCTURE METRICS REPORT ---")
        print(f"⏱️ Generation Duration: {generation_duration:.2f} seconds")
        print(f"📥 Base Boot VRAM:       {base_metrics['vram_gb']:.2f} GB")
        print(f"⚡ Peak Active VRAM:     {peak_metrics['vram_gb']:.2f} GB")
        print(f"🔺 Δ VRAM Footprint:     {delta_vram:.2f} GB")
        print(f"🔥 GPU Silicon Temp:     {peak_metrics['temp_c']}°C")
        print(f"🏎️ GPU Core Clock Speed: {peak_metrics['clock_mhz']} MHz")
        print(f"🌬️ Fan Speed Saturation: {peak_metrics['fan_pct']}%")
        print("-------------------------------------------\n")

        raw_text = response['response'].strip()

        # Remove markdown fences
        raw_text = re.sub(r"```(?:json)?", "", raw_text)
        raw_text = raw_text.replace("```", "").strip()
        print(type(raw_text))
        # Extract JSON object only
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)

        if not match:
            raise ValueError("No JSON object found in model output")

        json_text = match.group(0)

        # Remove trailing commas before } or ]
        json_text = re.sub(r",\s*([}\]])", r"\1", json_text)

        # Debug output if needed
        # print(json_text)

        result_json = json.loads(json_text)
        print(type(result_json))

        table_key = "table_data" if "table_data" in result_json else "visa_rows"

        return pd.DataFrame(result_json.get(table_key, [])), result_json.get("footer_timestamp", "Error")
        
    except Exception as e:
        print(f"⚠️ Local parser execution anomaly: {e}")
        try:
            print("\n===== RAW MODEL OUTPUT =====")
            print(raw_text)
            print("============================\n")
        except:
            pass
        # Return a structured error fallback so the main loop triggers a 1-hour cooldown sleep
        error_df = pd.DataFrame([{"Visa Location": "ERROR_PARSING_FAILED VAC"}])
        return error_df, "Error"


if __name__ == "__main__":
    start = datetime.now()
    
    # 1. Load Configurations globally at startup
    CONFIG_FILE = "config.json"
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config_data = json.load(f)
        print("⚙️ Successfully loaded keyword configurations from JSON layout.")

    params = config_data.get("PARAMS", {})
    print(params)
    json_schema = config_data.get("JSON_SCHEMA", {})

    # 2. Define Execution Target
    TEST_IMAGE_PATH = r"cropped_visa_table.png"

    print('📊 Original Image Metadata -> Width: 1920px, Height: 1080px')
    print("✂️ Cropping the new visa_slots.png to protect 6GB VRAM...")
    print("✂️ Safe Crop Execution Matrix -> Left: 192, Top: 270, Right: 1728, Bottom: 10026")
    

    # 3. Process Execution Matrix (This blocks and waits until finished)
    df, timestamp = fast_extract_table(TEST_IMAGE_PATH, params, json_schema)

    ps = requests.get("http://localhost:11434/api/ps").json()
    # print("Active models:", ps.get("models", []))


    # 4. Check hardware status immediately AFTER processing the image
    print("\n--------------------------------------------------")
    print("Checking active engine state during/after vision processing...")
    check_active_hardware()
    print("--------------------------------------------------")

    elapsed = datetime.now() - start
    print(f"Elapsed time in Generation by the Model: {elapsed}")
    print("--------------------------------------------------")

    # 5. Handle Terminal Feedback Display
    print("\n--- METADATA TIMESTAMP ---")
    print(timestamp)

    print("\n--- EXTRACTED DATA FRAME ---")
    print(df)
    
