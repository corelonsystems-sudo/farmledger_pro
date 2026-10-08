import io

from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.dispatch import receiver
from PIL import Image

from .models import Expense

MAX_WIDTH = 1200
MAX_HEIGHT = 1200
QUALITY = 85


@receiver(post_save, sender=Expense)
def compress_receipt_image(sender, instance, created, **kwargs):
    """Compress receipt images with Pillow after save."""
    if not instance.receipt_image:
        return

    try:
        img = Image.open(instance.receipt_image.path)
    except Exception:
        return

    if img.width <= MAX_WIDTH and img.height <= MAX_HEIGHT:
        return

    img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)

    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=QUALITY)
    buffer.seek(0)

    old_name = instance.receipt_image.name
    new_name = old_name.rsplit('.', 1)[0] + '.jpg'

    instance.receipt_image.save(
        new_name,
        ContentFile(buffer.getvalue()),
        save=False,
    )
    instance.save(update_fields=['receipt_image'])
