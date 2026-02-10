from flask import Flask, request, jsonify, send_from_directory
import os
import zipfile
import pandas as pd
import cv2
import re
from ultralytics import YOLO
import easyocr
from flask_cors import CORS
import logging
import shutil
import google.generativeai as genai
import time
import requests
from dotenv import load_dotenv
from fuzzywuzzy import fuzz, process

# Load environment variables
load_dotenv()

# Flask app setup
app = Flask(__name__)
CORS(app, resources={r"/process-files": {"origins": "http://localhost:8000"}})

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CLASS_MODEL_PATH = os.getenv("CLASS_MODEL_PATH")
DETECT_MODEL_PATH = os.getenv("DETECT_MODEL_PATH")
NODEJS_URL = os.getenv("NODEJS_URL", "http://localhost:3000/store-results")
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

# Validate critical environment variables
if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not set in .env")
    raise ValueError("GEMINI_API_KEY is required")

if not all(os.path.exists(p) for p in [CLASS_MODEL_PATH, DETECT_MODEL_PATH]):
    logger.error(f"Model files missing: CLASS={CLASS_MODEL_PATH}, DETECT={DETECT_MODEL_PATH}")
    raise FileNotFoundError("Model files missing!")

# Initialize models and APIs
genai.configure(api_key=GEMINI_API_KEY)
classification_model = YOLO(CLASS_MODEL_PATH)
detection_model = YOLO(DETECT_MODEL_PATH)
reader = easyocr.Reader(['en'], gpu=False)

# Indian states list
states = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal"
]

def parse_address(address):
    """Parse an Indian address into structured subfields with improved robustness."""
    if not address or not isinstance(address, str):
        return {
            "House Flat Number": None, "Town": None, "Street Road Name": None,
            "City": None, "Floor Number": None, "Country": "India", "PINCODE": None,
            "Premise Building Name": None, "Landmark": None, "State": None
        }
    
    tokens = [t.strip() for t in address.split() if t.strip()]
    subfields = {
        "House Flat Number": None, "Town": None, "Street Road Name": None,
        "City": None, "Floor Number": None, "Country": "India", "PINCODE": None,
        "Premise Building Name": None, "Landmark": None, "State": None
    }

    logger.debug(f"Input address: {address}")
    logger.debug(f"Tokens: {tokens}")

    # Find PINCODE and State (more flexible matching)
    for i in range(len(tokens) - 1, -1, -1):
        if tokens[i].isdigit() and len(tokens[i]) == 6:
            subfields["PINCODE"] = tokens[i]
            for j in range(i - 1, -1, -1):
                potential_state = " ".join(tokens[j:i])
                best_match, score = process.extractOne(potential_state, states, scorer=fuzz.ratio)
                if score >= 80:
                    subfields["State"] = best_match
                    if j > 0 and tokens[j - 1].isalnum():
                        subfields["City"] = tokens[j - 1]
                    break
            break

    # Extract Town (more flexible)
    if subfields["City"] and subfields["State"]:
        state_words = subfields["State"].split()
        state_index = -1
        for word in state_words:
            if word in tokens:
                state_index = tokens.index(word)
                break
        if state_index > 0:
            city_index = tokens.index(subfields["City"], 0, state_index)
            if city_index > 0:
                subfields["Town"] = tokens[city_index - 1] if tokens[city_index - 1].isalnum() else None

    # Determine start index after prefixes
    start_index = 0
    prefixes = ["c/o", "s/o", "w/o", "d/o", "cio:", "wio"]
    if tokens and any(tokens[0].lower().startswith(p) for p in prefixes):
        start_index = min(3, len(tokens))

    # Extract Floor Number (look for floor-related terms or infer from flat number)
    floor_keywords = ["floor", "flr", "floor no"]
    for i in range(start_index, len(tokens)):
        if any(kw in tokens[i].lower() for kw in floor_keywords) or (tokens[i].lower().startswith("floor") and i + 1 < len(tokens) and tokens[i + 1].isdigit()):
            floor_value = tokens[i]
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                floor_value += " " + tokens[i + 1]
            subfields["Floor Number"] = floor_value
            break
        # Improved inference for Flat/Fl/Apt followed by number using regex on full address
        elif re.search(r'(flat|fl|apt)\s*(\d+)', address.lower()):
            match = re.search(r'(flat|fl|apt)\s*(\d+)', address.lower())
            if match:
                floor_num = match.group(2)  # Extract the number (e.g., "404" from "Flat 404")
                subfields["Floor Number"] = f"{floor_num}th Floor"  # e.g., "4th Floor"
                # Set House Flat Number to the number part
                subfields["House Flat Number"] = floor_num
                break

    # Extract House/Flat Number (more flexible, including alphanumeric) as fallback
    if not subfields["House Flat Number"]:
        for i in range(start_index, len(tokens)):
            if any(char.isdigit() for char in tokens[i]) or (tokens[i].isalnum() and len(tokens[i]) < 10):
                if not subfields["Floor Number"] or not tokens[i].lower().startswith(("floor", "flr", "flat", "fl", "apt")):
                    subfields["House Flat Number"] = tokens[i]
                break

    # Extract Street/Road (more keywords and lenient matching)
    street_keywords = ["road", "street", "lane", "nagar", "colony", "avenue", "path", "chowk", "bazaar"]
    for i in range(start_index, len(tokens)):
        if any(kw in tokens[i].lower() for kw in street_keywords):
            street_start = max(start_index, i - 2)
            while street_start < i and tokens[street_start].isalnum():
                street_start += 1
            subfields["Street Road Name"] = " ".join(tokens[street_start:i + 1])
            break

    # Extract Landmark (expanded keywords and more context)
    landmark_keywords = ["near", "opposite", "behind", "beside", "next to", "close to", "adjacent to"]
    for i in range(start_index, len(tokens)):
        if any(kw in tokens[i].lower() for kw in landmark_keywords):
            landmark_end = min(i + 3, len(tokens))
            subfields["Landmark"] = " ".join(tokens[i:landmark_end])
            break

    # Extract Building Name (more flexible)
    street_index = (tokens.index(subfields["Street Road Name"].split()[0], start_index) if subfields["Street Road Name"] else
                   (tokens.index(subfields["City"], start_index) if subfields["City"] else len(tokens)))
    
    building_candidates = []
    for i in range(start_index, street_index):
        if (tokens[i][0].isupper() and tokens[i].isalnum() and not any(char.isdigit() for char in tokens[i]) and len(tokens[i]) > 2):
            building_candidates.append(tokens[i])
        elif subfields["House Flat Number"] and tokens[i] == subfields["House Flat Number"]:
            continue
        else:
            break
    if building_candidates:
        subfields["Premise Building Name"] = " ".join(building_candidates)

    logger.debug(f"Parsed address subfields: {subfields}")
    return subfields

def classify_image(image_path):
    """Classify an image as Aadhaar or Non-Aadhaar using YOLO."""
    try:
        results = classification_model(image_path)
        probs = results[0].probs
        top1, confidence = probs.top1, probs.top1conf.item()
        logger.debug(f"Classification: top1={top1}, confidence={confidence}")
        return "Aadhaar" if top1 == 0 and confidence >= 0.8 else "Non-Aadhaar"
    except Exception as e:
        logger.error(f"Classification error for {image_path}: {str(e)}")
        return "Error"

def extract_text(image_path):
    """Extract text from specific regions in an image with improved address handling."""
    try:
        image = cv2.imread(image_path)
        if image is None:
            logger.error(f"Failed to load image: {image_path}")
            return {}
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)

        results = detection_model(image_path)
        extracted = {}
        full_address = []

        for box in results[0].boxes.data.tolist():
            x1, y1, x2, y2 = map(int, box[:4])
            cropped = gray[y1:y2, x1:x2]
            if cropped.size == 0:
                logger.warning(f"Empty crop at box: ({x1}, {y1}, {x2}, {y2})")
                continue
            text = reader.readtext(cropped, detail=0, paragraph=True)
            if text:
                field_name = detection_model.names[int(box[5])]
                text_str = ' '.join(text).strip()
                logger.debug(f"Extracted {field_name}: {text_str}")
                if "name" in field_name.lower():
                    extracted["Name"] = text_str
                elif "uid" in field_name.lower() or "aadhaar" in field_name.lower():
                    extracted["UID"] = text_str.replace(" ", "").replace("-", "")
                elif "address" in field_name.lower():
                    full_address.extend([t for t in text_str.split() if t.strip()])
                elif "floor" in field_name.lower():
                    extracted["Floor Number"] = text_str
                else:
                    extracted[field_name] = text_str
        
        if full_address:
            address_text = ' '.join(full_address).strip()
            extracted["Address"] = address_text
            address_subfields = parse_address(address_text)
            extracted.update({k: v for k, v in address_subfields.items() if v is not None and v.strip()})
        
        logger.info(f"Extracted from {image_path}: {extracted}")
        return extracted
    except Exception as e:
        logger.error(f"OCR error for {image_path}: {str(e)}")
        return {}

def calculate_match_score_api(extracted_text, excel_text, retries=3, delay=2, field_type="text"):
    """Calculate similarity score between two texts with fallback."""
    if not extracted_text or not excel_text:
        return 0
    
    extracted_text = str(extracted_text).strip().lower()
    excel_text = str(excel_text).strip().lower()

    if field_type in ["names", "UIDs"]:
        return fuzz.ratio(extracted_text, excel_text)
    
    elif field_type == "addresses":
        time.sleep(10)  # Rate limit protection
        prompt = (
            f"Compare the following {field_type} and return a similarity score between 0 and 100 (integer only):\n"
            f"Text 1: {extracted_text}\nText 2: {excel_text}\n"
            "Instructions: Return only the score (e.g., 85), no text or code."
        )
        model = genai.GenerativeModel('gemini-1.5-pro')
        
        for attempt in range(retries):
            try:
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.0,
                        max_output_tokens=5
                    )
                )
                score = int(response.text.strip())
                if 0 <= score <= 100:
                    return score
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(delay * (2 ** attempt))
                    continue
        return max(fuzz.partial_ratio(extracted_text, excel_text), fuzz.token_sort_ratio(extracted_text, excel_text))

def calculate_score(extracted, excel):
    """Calculate weighted verification score with improved fallback and logging."""
    if not excel or not extracted:
        logger.warning("Empty excel or extracted data")
        return 0, {}
    
    weights = {
        "UID": 0.4, "Name": 0.3, "House Flat Number": 0.05, "Town": 0.05,
        "Street Road Name": 0.05, "City": 0.05, "Floor Number": 0.05,
        "Country": 0.05, "PINCODE": 0.05, "Premise Building Name": 0.05,
        "Landmark": 0.05, "State": 0.05
    }
    match_scores = {}
    weighted_scores = {}

    # Name comparison
    excel_name = str(excel.get("Name", "")).strip().lower()
    extracted_name = str(extracted.get("Name", "")).strip().lower()
    logger.debug(f"Comparing names: Excel={excel_name}, Extracted={extracted_name}")
    if excel_name and extracted_name:
        name_score = (100 if excel_name == extracted_name else
                     calculate_match_score_api(extracted_name, excel_name, field_type="names"))
        match_scores["Name Match Score"] = name_score
        weighted_scores["Name"] = name_score * weights["Name"]
    else:
        match_scores["Name Match Score"] = weighted_scores["Name"] = 0

    # UID comparison
    excel_uid = str(excel.get("UID", "")).replace(" ", "").replace("-", "").lower()
    extracted_uid = str(extracted.get("UID", "")).replace(" ", "").replace("-", "").lower()
    logger.debug(f"Comparing UIDs: Excel={excel_uid}, Extracted={extracted_uid}")
    if excel_uid and extracted_uid:
        uid_score = (100 if excel_uid == extracted_uid else
                    calculate_match_score_api(extracted_uid, excel_uid, field_type="UIDs"))
        match_scores["UID Match Score"] = uid_score
        weighted_scores["UID"] = uid_score * weights["UID"]
    else:
        match_scores["UID Match Score"] = weighted_scores["UID"] = 0

    # Address fields comparison
    address_fields = ["House Flat Number", "Town", "Street Road Name", "City",
                     "Floor Number", "Country", "PINCODE", "Premise Building Name",
                     "Landmark", "State"]
    for field in address_fields:
        excel_value = str(excel.get(field, "")).strip().lower()
        extracted_value = str(extracted.get(field, "")).strip().lower()
        logger.debug(f"Comparing {field}: Excel={excel_value}, Extracted={extracted_value}")
        if excel_value and extracted_value:
            fuzzy_score = max(fuzz.partial_ratio(extracted_value, excel_value), fuzz.token_sort_ratio(extracted_value, excel_value))
            score = (fuzzy_score if fuzzy_score >= 60 else
                    calculate_match_score_api(extracted_value, excel_value, field_type="addresses"))
            match_scores[f"{field} Match Score"] = max(0, min(100, score))
            weighted_scores[field] = score * weights[field]
        else:
            extracted_address = extracted.get("Address", "").lower()
            excel_address = " ".join([str(excel.get(f, "")).strip().lower() for f in address_fields if excel.get(f)]).strip()
            logger.debug(f"Full address fallback: Excel={excel_address}, Extracted={extracted_address}")
            if extracted_address and excel_address:
                address_score = max(fuzz.partial_ratio(extracted_address, excel_address), fuzz.token_sort_ratio(extracted_address, excel_address))
                match_scores[f"{field} Match Score"] = max(0, min(100, address_score))
                weighted_scores[field] = address_score * weights[field]
            else:
                match_scores[f"{field} Match Score"] = weighted_scores[field] = 0

    total_weight = sum(weights.values())
    overall_score = sum(weighted_scores.values()) / total_weight if weighted_scores else 0
    logger.info(f"Match scores: {match_scores}, Overall score: {overall_score}")
    return overall_score, match_scores

@app.route('/process-files', methods=['POST'])
def process_files():
    """Process uploaded ZIP and Excel files, treating sub-serial files (e.g., SR3_1, SR3_2) as a single SR3 entry."""
    try:
        logger.info("Received POST request to /process-files")
        if 'zipFile' not in request.files or 'excelFile' not in request.files:
            logger.error("Missing zipFile or excelFile in request")
            return jsonify({"error": "Missing files"}), 400

        zip_file = request.files['zipFile']
        excel_file = request.files['excelFile']

        if not zip_file.filename.lower().endswith('.zip') or \
           not excel_file.filename.lower().endswith(('.xlsx', '.xls')):
            logger.error("Invalid file format: ZIP or Excel")
            return jsonify({"error": "Invalid file format"}), 400

        UPLOAD_DIR = 'uploads'
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        
        paths = {
            'zip': os.path.join(UPLOAD_DIR, 'temp.zip'),
            'excel': os.path.join(UPLOAD_DIR, 'temp.xlsx'),
            'extracted': os.path.join(UPLOAD_DIR, 'extracted'),
            'output': os.path.join(UPLOAD_DIR, 'verification_results.xlsx')
        }
        
        if os.path.exists(paths['extracted']):
            shutil.rmtree(paths['extracted'])
        os.makedirs(paths['extracted'], exist_ok=True)

        zip_file.save(paths['zip'])
        excel_file.save(paths['excel'])

        with zipfile.ZipFile(paths['zip'], 'r') as z:
            z.extractall(paths['extracted'])
            logger.info(f"Extracted ZIP to {paths['extracted']}")

        df = pd.read_excel(paths['excel'])
        df['SrNo'] = df['SrNo'].astype(str).str.strip()
        df = df.fillna('')
        logger.debug(f"Loaded Excel data with columns: {df.columns.tolist()}")

        if 'SrNo' not in df.columns:
            logger.error("Excel file must have 'SrNo' column")
            return jsonify({"error": "Excel file must have 'SrNo' column"}), 400

        results, excel_data = [], []
        
        # Group images by base serial number (e.g., SR3_1 and SR3_2 under SR3)
        image_groups = {}
        for root, _, files in os.walk(paths['extracted']):
            for file in files:
                if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                    img_path = os.path.join(root, file)
                    serial_number = os.path.splitext(file)[0].strip()
                    base_serial_number = serial_number.split('_')[0]  # e.g., SR3_1 -> SR3
                    
                    if base_serial_number not in image_groups:
                        image_groups[base_serial_number] = []
                    image_groups[base_serial_number].append((file, img_path, serial_number))
                    logger.debug(f"Image {file} mapped to base serial {base_serial_number}")

        # Process each group of images and use the best score
        for base_serial_number, images in image_groups.items():
            logger.info(f"Processing group for base serial: {base_serial_number}, Images: {[img[0] for img in images]}")
            
            matching_rows = df[df['SrNo'] == base_serial_number]
            if matching_rows.empty:
                for file, _, serial_number in images:
                    result = {
                        "file": file, "status": "Rejected", "document_type": "Unknown",
                        "final_remark": f"Base serial number {base_serial_number} not found in Excel"
                    }
                    results.append(result)
                    excel_data.append({**result, "SrNo": serial_number})
                logger.warning(f"No matching row found for base serial {base_serial_number}")
                continue
            
            excel_row = matching_rows.iloc[0].to_dict()
            logger.debug(f"Excel row for {base_serial_number}: {excel_row}")
            
            best_score = 0
            best_extracted = {}
            best_match_scores = {}
            best_classification = None
            processed_files = []

            # Process each image in the group and find the best score
            for file, img_path, serial_number in images:
                classification = classify_image(img_path)
                extracted = extract_text(img_path)
                processed_files.append(file)
                
                if not extracted.get("Name"):
                    logger.error(f"Name not extracted for {file}")
                    continue
                
                score, match_scores = calculate_score(extracted, excel_row)
                if score > best_score:
                    best_score = score
                    best_extracted = extracted
                    best_match_scores = match_scores
                    best_classification = classification
            
            if best_score > 0:  # Only report if at least one image was successfully processed
                status = "Verified" if best_score >= 85 else "Rejected"
                # Modified logic: If document_type is Non-Aadhaar, set final_remark to "Non Aadhaar"
                if best_classification == "Non-Aadhaar":
                    remark = "Non Aadhaar"
                else:
                    remark = "Matched" if best_score >= 85 else "Low match score"
                
                result = {
                    "file": ", ".join(processed_files),  # List all processed files
                    "status": status,
                    "document_type": best_classification,
                    "final_remark": f"{remark} (processed {len(processed_files)} images)" if best_classification != "Non-Aadhaar" else remark,
                    "score": round(best_score, 2)
                }
                results.append(result)
                
                excel_entry = {
                    "SrNo": base_serial_number,
                    **{f"{k}": excel_row.get(k, "") for k in [
                        "House Flat Number", "Town", "Street Road Name", "City",
                        "Country", "PINCODE", "Premise Building Name",
                        "Landmark", "State", "Name", "UID"]},  # Exclude Floor Number
                    **{f"{k} Match Score": best_match_scores.get(f"{k} Match Score", 0)
                      for k in ["House Flat Number", "Town", "Street Road Name", "City",
                              "Country", "PINCODE", "Premise Building Name",
                              "Landmark", "State", "Name", "UID"]},  # Exclude Floor Number Match Score
                    "Extracted Name": best_extracted.get("Name", ""),
                    "Extracted UID": best_extracted.get("UID", ""),
                    "Extracted Address": best_extracted.get("Address", ""),
                    "Overall Match": round(best_score, 2),
                    "Final Remarks": remark,
                    "Document Type": best_classification
                }
                excel_data.append(excel_entry)
                logger.info(f"Processed group {base_serial_number} with best score: {round(best_score, 2)}")
            else:
                for file, _, serial_number in images:
                    result = {
                        "file": file, "status": "Rejected", "document_type": "Unknown",
                        "final_remark": "No valid data extracted from any image"
                    }
                    results.append(result)
                    excel_data.append({**result, "SrNo": serial_number})
                logger.error(f"No valid data extracted for group {base_serial_number}")

        output_df = pd.DataFrame(excel_data)
        output_df.to_excel(paths['output'], index=False)
        logger.info(f"Saved results to {paths['output']}")

        # Send to Node.js backend
        node_data = [
            {
                "name": entry.get("Extracted Name", ""),
                "uid": entry.get("Extracted UID", ""),
                "address": entry.get("Extracted Address", ""),
                "final_remark": entry.get("Final Remarks", ""),
                "document_type": entry.get("Document Type", "")
            } for entry in excel_data
        ]
        try:
            response = requests.post(NODEJS_URL, json=node_data, timeout=10)
            response.raise_for_status()
            logger.info(f"Node.js API call successful: Status {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Node.js API call failed: {str(e)}")

        # Cleanup step removed to retain uploaded files in uploads folder
        # for path in [paths['zip'], paths['excel']]:
        #     if os.path.exists(path):
        #         os.remove(path)
        #         logger.debug(f"Removed temporary file: {path}")
        # if os.path.exists(paths['extracted']):
        #     shutil.rmtree(paths['extracted'])
        #     logger.debug(f"Removed extracted directory: {paths['extracted']}")

        logger.info("Successfully processed /process-files request")
        return jsonify(results)

    except Exception as e:
        logger.error(f"Processing error: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/download-results', methods=['GET'])
def download_results():
    """Serve the verification results Excel file."""
    try:
        logger.info("Serving download-results request")
        return send_from_directory('uploads', 'verification_results.xlsx', as_attachment=True)
    except Exception as e:
        logger.error(f"Error serving file: {str(e)}")
        return jsonify({"error": "File not found"}), 404

if __name__ == '__main__':
    logger.info("Starting Flask server")
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=True)