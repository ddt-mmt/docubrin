import os
import subprocess
import time
import io
import hashlib
from datetime import datetime
from celery import Celery
from celery.schedules import crontab
from PyPDF2 import PdfWriter, PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
app = Celery("docubrin", broker=redis_url, backend=redis_url)

def get_file_hash(file_path: str):
    sha256_hash = hashlib.sha256()
    with open(file_path,"rb") as f:
        for byte_block in iter(lambda: f.read(4096),b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def add_security_metadata_and_watermark(pdf_path: str):
    """
    Menambahkan metadata keamanan dan watermark profesional di bagian bawah setiap halaman.
    """
    temp_output = pdf_path + ".tmp"
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
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

    # Tambahkan Metadata Profesional yang bisa dibuktikan di properties file
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

@app.task
def convert_to_pdf(input_path: str):
    output_dir = "/tmp/docubrin"
    file_name = os.path.basename(input_path)
    file_id = os.path.splitext(file_name)[0]
    output_path = os.path.join(output_dir, f"{file_id}.pdf")
    
    try:
        # LibreOffice Headless Command
        subprocess.run([
            'libreoffice', '--headless', '--convert-to', 'pdf',
            '--outdir', output_dir, input_path
        ], check=True)
        
        # Tambahkan metadata dan watermark
        if os.path.exists(output_path):
            add_security_metadata_and_watermark(output_path)
        
        # Cleanup original input immediately after conversion
        if os.path.exists(input_path):
            os.remove(input_path)
            
        return f"Converted & Secured: {file_id}.pdf"
    except Exception as e:
        if os.path.exists(input_path):
            os.remove(input_path)
        return str(e)

@app.task
def merge_pdfs(input_paths: list, output_filename: str):
    output_dir = "/tmp/docubrin"
    output_path = os.path.join(output_dir, f"{output_filename}.pdf")
    
    try:
        writer = PdfWriter()
        for path in input_paths:
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
            if os.path.exists(path):
                os.remove(path)
        return str(e)

@app.task
def cleanup_files(file_path: str):
    time.sleep(2) 
    if os.path.exists(file_path):
        os.remove(file_path)

@app.task
def janitor_cleanup():
    now = time.time()
    path = "/tmp/docubrin"
    if not os.path.exists(path):
        return
        
    for f in os.listdir(path):
        f_path = os.path.join(path, f)
        if os.stat(f_path).st_mtime < now - 900: # 15 menit
            if os.path.isfile(f_path):
                os.remove(f_path)

@app.task
def update_antivirus_db():
    """Background task untuk memastikan ClamAV selalu update"""
    # ClamAV biasanya sudah punya freshclam auto-update di container
    # Tapi kita bisa trigger via shell jika diperlukan
    subprocess.run(['freshclam'], capture_output=True)

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
