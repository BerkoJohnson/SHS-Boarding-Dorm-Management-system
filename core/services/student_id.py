import base64
from io import BytesIO

import qrcode
from qrcode.image.svg import SvgPathImage


def make_qr_data_uri(value):
    code = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=5,
        border=2,
    )
    code.add_data(value)
    code.make(fit=True)
    image = code.make_image(image_factory=SvgPathImage)
    buffer = BytesIO()
    image.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
