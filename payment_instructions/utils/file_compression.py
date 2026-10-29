import os
from PIL import Image
import fitz
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile


class FileCompressor:
    # Lower quality for maximum compression while maintaining readability
    JPEG_QUALITY = 70  # 70% quality is usually enough for transaction proofs
    MAX_WIDTH = 1200  # Maximum width in pixels
    MAX_HEIGHT = 1600  # Maximum height in pixels
    TARGET_SIZE_KB = 50  # Target file size in KB

    @staticmethod
    def compress_image(image_file):
        """Compress image files while maintaining readability for bank proofs"""
        try:
            # Open the image
            img = Image.open(image_file)

            # Convert to RGB if necessary (removes alpha channel)
            if img.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background

            # Calculate new dimensions while maintaining aspect ratio
            img.thumbnail(
                (FileCompressor.MAX_WIDTH, FileCompressor.MAX_HEIGHT), Image.Resampling.LANCZOS
            )

            # Progressive compression to reach target size
            quality = FileCompressor.JPEG_QUALITY
            output = BytesIO()

            while quality > 30:  # Minimum quality threshold
                output = BytesIO()
                img.save(output, format="JPEG", quality=quality, optimize=True)
                size_kb = output.tell() / 1024

                if size_kb <= FileCompressor.TARGET_SIZE_KB:
                    break

                quality -= 10

                # If still too large, reduce dimensions
                if quality <= 50 and size_kb > FileCompressor.TARGET_SIZE_KB:
                    new_width = int(img.width * 0.8)
                    new_height = int(img.height * 0.8)
                    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            output.seek(0)

            # Generate new filename
            name = os.path.splitext(image_file.name)[0]
            new_filename = f"{name}_compressed.jpg"

            return InMemoryUploadedFile(
                output, "ImageField", new_filename, "image/jpeg", output.tell(), None
            )

        except Exception:
            return image_file

    @staticmethod
    def pdf_to_jpeg(pdf_file, dpi=100, quality=75, single_page=True):
        try:
            pdf_file.seek(0)
            doc = fitz.open(stream=pdf_file.read(), filetype="pdf")

            zoom = dpi / 72  # default PDF DPI is 72
            mat = fitz.Matrix(zoom, zoom)

            page = doc[0]
            pix = page.get_pixmap(matrix=mat)
            output = BytesIO()
            pix.pil_save(output, format="JPEG", optimize=True, quality=quality)
            output.seek(0)

            new_filename = os.path.splitext(pdf_file.name)[0] + ".jpg"
            return InMemoryUploadedFile(
                output, "FileField", new_filename, "image/jpeg", output.getbuffer().nbytes, None
            )

        except Exception:
            return pdf_file

    @staticmethod
    def compress_file(file_obj):
        """Main method to compress any supported file"""
        if not file_obj:
            return None

        file_obj.seek(0)
        content_type = file_obj.content_type.lower()

        if content_type in ["image/jpeg", "image/jpg", "image/png", "image/gif"]:
            return FileCompressor.compress_image(file_obj)
        else:
            return FileCompressor.pdf_to_jpeg(file_obj)
