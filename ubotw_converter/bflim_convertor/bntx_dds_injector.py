from . import bntx as BNTX
from pathlib import Path
from . import bflim_extract
from . import addrlib
from types import SimpleNamespace


def _adapt_flim_for_target(flim, target_tex):
    _, raw = bflim_extract.get_deswizzled_data(flim)
    adapted = SimpleNamespace(**flim.__dict__)
    adapted.data = raw
    adapted.realSize = len(raw)

    if target_tex.format != 0x0201:
        return adapted

    pixel_count = flim.width * flim.height
    if len(raw) == pixel_count:
        channel_data = raw
    elif len(raw) == pixel_count * 2:
        channel_data = raw[::2]
    elif len(raw) == pixel_count * 4:
        channel_data = raw[3::4]
    else:
        channel_data = raw[:pixel_count]

    adapted.format = 0x01
    adapted.dds_format = 0x0201
    adapted.data = channel_data
    adapted.realSize = len(channel_data)
    adapted.compSel = target_tex.compSel2.copy()
    return adapted

def tex_inject(bntx: Path, bflim: Path):
    # Read the bflim file
    with open(bflim, 'rb') as f:
        inb = f.read()

    # Format and store the flim bytes
    flim = bflim_extract.readFLIM(inb)

    # Read the bntx file
    bntx_file = BNTX.read(bntx)

    # Store the name, target, textures and tex_names of the bntx file
    name, target, textures = bntx_file

    # Store the texture name as a variable
    o_tex = ' '.join([x for x in textures.keys() if x == bflim.stem])
    target_tex = textures[o_tex]

    # Preserve slot metadata that must not be rewritten from the BFLIM source
    target_format = target_tex.format
    target_comp_sel = target_tex.compSel.copy()
    target_comp_sel_raw = target_tex.compSel2.copy()

    # Set up the variables for import the dds file
    tile_mode = target_tex.tileMode
    srgb = target_tex.format & 0xFF == 6
    sparse_binding = bool(target_tex.sparseBinding)
    sparse_residency = bool(target_tex.sparseResidency)

    old_tex_size = target_tex.imageSize
    old_tex_num_mips = target_tex.numMips
    source_flim = _adapt_flim_for_target(flim, target_tex)
    tex_ = BNTX.inject(target_tex, tile_mode, srgb, sparse_binding, sparse_residency, old_tex_size, source_flim)

    if tex_:
        tex_.format = target_format
        tex_.compSel = target_comp_sel_raw.copy()
        tex_.compSel2 = target_comp_sel_raw.copy()
        # Write to the bntx
        BNTX.writeTex(bntx, tex_, old_tex_size, old_tex_num_mips)
        return True
    return False