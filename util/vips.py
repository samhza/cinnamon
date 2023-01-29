import pyvips
from util.tmpfile import reserve as mk_tempfile
import os


def outline(img: pyvips.Image, radius: int) -> pyvips.Image:
    img = img.embed(
        radius, radius, img.width + radius * 2, img.height + radius * 2, extend="copy"
    )
    mask = pyvips.Image.gaussmat(radius / 2, 0.0001, separable=True) * 10
    img = img[3].convsep(mask).cast("uchar")
    return img.new_from_image([0, 0, 0]).bandjoin(img)


def meme(width: int, height: int, top: str, bottom: str) -> pyvips.Image:
    size = width // 14
    rad = max(1, width / 1000)

    def meme_text(text: str) -> pyvips.Image | None:
        if text == "":
            return None
        text = pyvips.Image.text(
            text,
            font=f"Impact Bold {width/9}",
            width=width * 0.95,
            height=height * 0.95 / 3,
            align="centre",
            rgba=True,
            dpi=72,
        )
        text = text.new_from_image([255, 255, 255]).bandjoin(text[3])
        text = outline(text, rad).composite2(text, "over", x=rad, y=rad)
        pad = width / 20
        return text.embed(pad, pad, text.width + pad * 2, text.height + pad * 2)

    toptext = meme_text(top)
    bottomtext = meme_text(bottom)
    text: None | pyvips.Image = None
    if toptext is not None:
        text = toptext.gravity("north", width, height)
    if bottomtext is not None:
        bt = bottomtext.gravity("south", width, height)
        if text is None:
            text = bt
        else:
            text = text.composite2(bt, "over")
    return text


def dimensions(file: str) -> tuple[int, int]:
    img = pyvips.Image.new_from_file(file)
    return img.width, img.get_page_height()


def write_image(image: pyvips.Image, suffix: str) -> str:
    tf = mk_tempfile(suffix)
    try:
        image.write_to_file(tf)
        return tf
    except Exception as e:
        os.remove(tf)
