import os
import subprocess
import time
import io
import hashlib
import shutil
from datetime import datetime
from celery import Celery
from celery.schedules import crontab
from PyPDF2 import PdfWriter, PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
app = Celery("docubrin", broker=redis_url, backend=redis_url)

TEMP_DIR = "/tmp/docubrin"

def get_file_hash(file_path: str):
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path,"rb") as f:
            for byte_block in iter(lambda: f.read(4096),b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return "unknown"

def add_security_metadata_and_watermark(pdf_path: str):
    """
    Menambahkan metadata keamanan dan watermark profesional di bagian bawah setiap halaman.
    """
    if not os.path.exists(pdf_path):
        return

    temp_output = pdf_path + ".tmp"
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        # Buat watermark halus menggunakan ReportLab
        packet = io.BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)
        can.setFont("Helvetica-Bold", 7)
        can.setFillColorRGB(0.5, 0.5, 0.5, alpha=0.3) # Abu-abu transparan
        watermark_text = f"SECURED BY DOCUBRIN | VERIFIED CLEAN: {scan_time} | ZERO-RETENTION POLICY"
        can.drawString(30, 15, watermark_text)
        can.save()
        packet.seek(0)
        watermark_pdf = PdfReader(packet)
        watermark_page = watermark_pdf.pages[0]

        reader = PdfReader(pdf_path)
        writer = PdfWriter()

        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)

        # Tambahkan Metadata Profesional
        writer.add_metadata({
            '/Author': 'Sistem Keamanan DocuBRIN',
            '/Producer': 'DocuBRIN Security Engine v2.0',
            '/Creator': 'BRIN Digital Workspace',
            '/Subject': 'Secured & Cleaned Document',
            '/Keywords': 'BRIN, Verified, Secure, Cleaned, Anti-Malware',
            '/DocuBRIN-Status': 'Verified Clean / Malware Scanned',
            '/DocuBRIN-ScanTime': scan_time,
            '/DocuBRIN-VerificationID': f"BRIN-SEC-{int(time.time())}",
            '/DocuBRIN-SHA256': get_file_hash(pdf_path)
        })

        with open(temp_output, "wb") as f:
            writer.write(f)
        
        os.replace(temp_output, pdf_path)
    except Exception as e:
        print(f"Error adding watermark: {e}")
        if os.path.exists(temp_output):
            os.remove(temp_output)

@app.task
def convert_to_pdf(input_path: str):
    # Security: Ensure input_path is within TEMP_DIR
    if not os.path.abspath(input_path).startswith(TEMP_DIR):
        return "Error: Unauthorized path access"

    file_name = os.path.basename(input_path)
    file_id, ext = os.path.splitext(file_name)
    output_path = os.path.join(TEMP_DIR, f"{file_id}.pdf")
    
    try:
        if ext.lower() == '.pdf':
            # Jika sudah PDF, kita tidak perlu konversi, cukup pastikan lokasinya benar
            # Jika input_path sudah sama dengan output_path, tidak perlu di-rename
            if input_path != output_path:
                shutil.move(input_path, output_path)
        else:
            # LibreOffice Headless Command
            subprocess.run([
                'libreoffice', '--headless', '--invisible', '--nodefault', '--nologo',
                '--convert-to', 'pdf', '--outdir', TEMP_DIR, input_path
            ], check=True, timeout=60)
            
            # Cleanup original input immediately after conversion
            if os.path.exists(input_path):
                os.remove(input_path)
        
        # Tambahkan metadata dan watermark (Berlaku untuk semua)
        if os.path.exists(output_path):
            add_security_metadata_and_watermark(output_path)
            
        return f"Converted & Secured: {file_id}.pdf"
    except subprocess.TimeoutExpired:
        if os.path.exists(input_path): os.remove(input_path)
        return "Error: Conversion timed out (possible complex/malicious document)"
    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return f"Error: {str(e)}"

@app.task
def merge_pdfs(input_paths: list, output_filename: str):
    output_path = os.path.join(TEMP_DIR, f"{output_filename}.pdf")
    
    try:
        writer = PdfWriter()
        for path in input_paths:
            if not os.path.abspath(path).startswith(TEMP_DIR):
                continue
            writer.append(path)
            if os.path.exists(path):
                os.remove(path)
                
        # Tambahkan Metadata
        writer.add_metadata({
            '/Author': 'Sistem DocuBRIN',
            '/Producer': 'DocuBRIN Security Engine',
            '/DocuBRIN-Status': 'Merged & Scanned'
        })
        
        with open(output_path, "wb") as f:
            writer.write(f)
            
        # Watermarking hasil merge
        add_security_metadata_and_watermark(output_path)
            
        return f"Merged & Secured: {output_filename}.pdf"
    except Exception as e:
        for path in input_paths:
            if os.path.exists(path) and os.path.abspath(path).startswith(TEMP_DIR):
                os.remove(path)
        return f"Error: {str(e)}"

@app.task
def cleanup_files(file_path: str):
    # Only cleanup if in TEMP_DIR
    if os.path.exists(file_path) and os.path.abspath(file_path).startswith(TEMP_DIR):
        time.sleep(2) 
        if os.path.exists(file_path):
            os.remove(file_path)

@app.task
def janitor_cleanup():
    now = time.time()
    if not os.path.exists(TEMP_DIR):
        return
        
    for f in os.listdir(TEMP_DIR):
        f_path = os.path.join(TEMP_DIR, f)
        # Ensure we only delete files in the temp dir
        if not os.path.abspath(f_path).startswith(TEMP_DIR):
            continue
            
        if os.stat(f_path).st_mtime < now - 900: # 15 menit
            if os.path.isfile(f_path):
                try:
                    os.remove(f_path)
                except Exception:
                    pass

@app.task
def update_antivirus_db():
    """Background task untuk memastikan ClamAV selalu update"""
    # Note: freshclam usually requires root or specific user. 
    # In clamav container, it's already running.
    # This is just an extra precaution if the worker has access.
    try:
        subprocess.run(['freshclam'], capture_output=True, timeout=300)
    except Exception:
        pass

app.conf.beat_schedule = {
    'cleanup-every-15-minutes': {
        'task': 'tasks.janitor_cleanup',
        'schedule': crontab(minute='*/15'),
    },
    'update-av-every-30-minutes': {
        'task': 'tasks.update_antivirus_db',
        'schedule': crontab(minute='*/30'),
    }
}
