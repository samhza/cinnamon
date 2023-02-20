import pyvips
from pyvips import GValue
from util.tmpfile import reserve as mk_tempfile
import os


def outline(img: pyvips.Image, radius: int) -> pyvips.Image:
    img = img.embed(
        radius, radius, img.width + radius * 2, img.height + radius * 2, extend="copy"
    )
    mask = pyvips.Image.gaussmat(radius / 2, 0.0001, separable=True) * 10
    img = img[3].convsep(mask).cast("uchar")
    return img.new_from_image([0, 0, 0]).bandjoin(img)

def caption(width: int, text: str) -> pyvips.Image:
    text = pyvips.Image.text(
        text,
        rgba=True,
        align="centre",
        # TODO use Futura
        font=f'DejaVu Sans {width//10}',
        width=width,
    )
    text = text.new_from_image([0,0,0]).bandjoin(text[3])
    text = text.gravity("centre", width, text.height + width//10)
    text = text.new_from_image([255,255,255]).composite2(text, "over")

    return text


def vstack(top: pyvips.Image, img: pyvips.Image) -> pyvips.Image:
    caption_height = top.height
    top = top.embed(0, 0, img.width, img.height + caption_height, extend="black")
    if not img.hasalpha():
        img = img.bandjoin(255)
    replicated = top.replicate(1, img.get_n_pages())
    page_height = img.get_page_height()
    for i in range(img.get_n_pages()):
        frame = img.crop(0, i * page_height, img.width, page_height)
        replicated = replicated.insert(frame, 0, i * top.height + caption_height)
    replicated.set_type(GValue.gint_type, 'page-height', top.height)
    return replicated


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
