import os
import uuid
import clamd
import shutil
from typing import List
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from tasks import convert_to_pdf, merge_pdfs, cleanup_files

app = FastAPI(title="DocuBRIN API")

# Serve static files
app.mount("/static", StaticFiles(directory="static"), name="static")

CLAMD_HOST = os.getenv("CLAMD_HOST", "clamav")
MAX_FILE_SIZE = 20 * 1024 * 1024 # 20MB

def scan_file(file_path: str):
    try:
        cd = clamd.ClamdNetworkSocket(host=CLAMD_HOST, port=3310)
        cd.ping()
        # Menggunakan instream agar data file dikirim langsung lewat network ke ClamAV
        # Ini lebih reliabel daripada scan(path) jika folder tidak terbagi sempurna
        with open(file_path, 'rb') as f:
            result = cd.instream(f)
            
        if result and 'stream' in result and result['stream'][0] == 'FOUND':
            return False, result['stream'][1]
        return True, None
    except Exception as e:
        print(f"[SECURITY ENGINE] Critical Error: Antivirus connectivity issue.")
        # Fail-Closed: Jika scanner mati, kita tidak izinkan file masuk demi keamanan
        error_detail = (
            "Layanan pemindaian antivirus tidak tersedia. Sesuai standar ISO 27001 dan NIST SP 800-83, "
            "DocuBRIN Security Engine tidak dapat memproses dokumen tanpa verifikasi keamanan penuh demi integritas sistem."
        )
        return False, error_detail

@app.post("/upload/convert")
async def upload_for_conversion(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    # 1. Instantly check size (FastAPI validation)
    file.file.seek(0, 2)
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File too large (Max 20MB)")

    # 2. Save to temp for scanning
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_id = f"brindoc_{timestamp}_{uuid.uuid4().hex[:6]}"
    ext = os.path.splitext(file.filename)[1]
    input_path = f"/tmp/docubrin/{file_id}{ext}"
    
    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # 3. Virus Scan
    is_clean, virus_info = scan_file(input_path)
    if not is_clean:
        if os.path.exists(input_path):
            os.remove(input_path)
        
        # Pesan edukatif sesuai standar keamanan
        rejection_message = (
            f"Dokumen Ditolak: Terdeteksi ancaman keamanan ({virus_info}). "
            "Sesuai standar NIST SP 800-83 & ISO 27001, DocuBRIN Security Engine menerapkan kebijakan 'Reject & Destroy' "
            "demi melindungi integritas data Anda. File bervirus tidak disarankan untuk 'dibersihkan' "
            "karena risiko persistensi malware. Silakan hubungi Administrator Sistem Anda."
        )
        raise HTTPException(status_code=400, detail=rejection_message)

    # 4. Trigger Celery Task
    task = convert_to_pdf.delay(input_path)
    
    return {"task_id": task.id, "status": "processing", "file_id": file_id}

@app.post("/upload/merge")
async def upload_for_merging(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...)
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    file_ids = []
    input_paths = []
    
    for file in files:
        # 1. Size check
        file.file.seek(0, 2)
        if file.file.tell() > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"File {file.filename} too large")
        file.file.seek(0)

        # 2. Save
        file_id = str(uuid.uuid4())
        ext = os.path.splitext(file.filename)[1].lower()
        if ext != '.pdf':
            # Cleanup already saved files
            for p in input_paths:
                if os.path.exists(p): os.remove(p)
            raise HTTPException(status_code=400, detail="Only PDF files are supported for merging")
            
        input_path = f"/tmp/docubrin/{file_id}{ext}"
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 3. Virus Scan
        is_clean, virus_info = scan_file(input_path)
        if not is_clean:
            # Cleanup already saved files
            for p in input_paths:
                if os.path.exists(p): os.remove(p)
            os.remove(input_path)
            
            rejection_message = (
                f"File {file.filename} ditolak: Terdeteksi ancaman ({virus_info}). "
                "Sesuai standar NIST SP 800-83 & ISO 27001, kami menerapkan kebijakan 'Reject & Destroy' "
                "demi keamanan sistem. File bervirus tidak disarankan untuk 'dibersihkan'. "
                "Silakan periksa kembali file Anda secara lokal."
            )
            raise HTTPException(status_code=400, detail=rejection_message)

        input_paths.append(input_path)
        file_ids.append(file_id)

    # 4. Trigger Merge Task
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    merge_id = f"brindoc_merged_{timestamp}_{uuid.uuid4().hex[:6]}"
    task = merge_pdfs.delay(input_paths, merge_id)
    
    return {"task_id": task.id, "status": "processing", "file_id": merge_id}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    from tasks import app as celery_app
    res = celery_app.AsyncResult(task_id)
    return {"status": res.status, "result": res.result}

@app.get("/download/{file_id}")
async def download_result(file_id: str, background_tasks: BackgroundTasks):
    output_path = f"/tmp/docubrin/{file_id}.pdf"
    
    if os.path.exists(output_path):
        # Immediate removal after transfer starts (FastAPI FileResponse handles the stream)
        # However, for safety, we use a background task with a small delay
        response = FileResponse(output_path, media_type='application/pdf', filename=f"{file_id}.pdf")
        background_tasks.add_task(cleanup_files, output_path)
        return response
    
    raise HTTPException(status_code=404, detail="File not ready or expired")

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html", "r") as f:
        return f.read()
