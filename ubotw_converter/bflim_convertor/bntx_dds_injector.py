from . import bntx as BNTX
from pathlib import Path
from . import bflim_extract
from . import addrlib
from types import SimpleNamespace

_SYROOT_TYPES = None


def _get_syroot_types():
    global _SYROOT_TYPES
    if _SYROOT_TYPES is not None:
        return _SYROOT_TYPES

    import clr
    import System

    dll_path = Path(__file__).resolve().parents[3] / "BfresLibrary" / "Libraries" / "Syroot.NintenTools.NSW.Bntx.dll"
    clr.AddReference(str(dll_path))

    from Syroot.NintenTools.NSW.Bntx import BntxFile, Texture, ResDict, UserData
    from System.Collections.Generic import List

    def enum(type_name: str, value: int):
        enum_type = System.Type.GetType(type_name + ", Syroot.NintenTools.NSW.Bntx")
        return System.Enum.ToObject(enum_type, value)

    _SYROOT_TYPES = {
        "System": System,
        "BntxFile": BntxFile,
        "Texture": Texture,
        "ResDict": ResDict,
        "UserData": UserData,
        "List": List,
        "enum": enum,
    }
    return _SYROOT_TYPES


def _deswizzle_flim_payload(flim):
    _, raw = bflim_extract.get_deswizzled_data(flim)
    adapted = SimpleNamespace(**flim.__dict__)
    adapted.data = raw
    adapted.realSize = len(raw)
    return adapted


def _default_channel_mapping(format_: int):
    if format_ == 0x0201:
        return 2, 2, 2, 1
    return 2, 3, 4, 5


def _append_new_texture_entry(bntx: Path, bflim: Path, flim) -> bool:
    types = _get_syroot_types()
    System = types["System"]
    BntxFile = types["BntxFile"]
    Texture = types["Texture"]
    ResDict = types["ResDict"]
    UserData = types["UserData"]
    List = types["List"]
    enum = types["enum"]

    source_flim = _deswizzle_flim_payload(flim)
    tex_ = BNTX.inject(SimpleNamespace(target=1), 0, flim.format in BNTX.SRGB_FORMATS, False, False, 0, source_flim)
    if not tex_:
        return False

    new_tex = Texture()
    new_tex.Name = bflim.stem
    new_tex.Path = None

    chan_r, chan_g, chan_b, chan_a = _default_channel_mapping(tex_.format)
    new_tex.ChannelRed = enum("Syroot.NintenTools.NSW.Bntx.GFX.ChannelType", chan_r)
    new_tex.ChannelGreen = enum("Syroot.NintenTools.NSW.Bntx.GFX.ChannelType", chan_g)
    new_tex.ChannelBlue = enum("Syroot.NintenTools.NSW.Bntx.GFX.ChannelType", chan_b)
    new_tex.ChannelAlpha = enum("Syroot.NintenTools.NSW.Bntx.GFX.ChannelType", chan_a)
    new_tex.Width = tex_.width
    new_tex.Height = tex_.height
    new_tex.MipCount = tex_.numMips
    new_tex.Format = enum("Syroot.NintenTools.NSW.Bntx.GFX.SurfaceFormat", tex_.format)
    new_tex.UseSRGB = (tex_.format & 0xFF) == 0x06
    new_tex.Depth = 1
    new_tex.TileMode = enum("Syroot.NintenTools.NSW.Bntx.GFX.TileMode", tex_.tileMode)
    new_tex.Swizzle = 0xD0000
    new_tex.Alignment = tex_.alignment
    new_tex.Pitch = 0
    new_tex.Dim = enum("Syroot.NintenTools.NSW.Bntx.GFX.Dim", 2)
    new_tex.SurfaceDim = enum("Syroot.NintenTools.NSW.Bntx.GFX.SurfaceDim", 1)
    new_tex.MipOffsets = System.Array[System.Int64]([0])

    outer = List[List[System.Array[System.Byte]]]()
    inner = List[System.Array[System.Byte]]()
    inner.Add(System.Array[System.Byte](bytearray(tex_.data)))
    outer.Add(inner)
    new_tex.TextureData = outer

    new_tex.Flags = tex_.sparseResidency << 2 | tex_.sparseBinding << 1 | tex_.readTexLayout
    new_tex.ImageSize = tex_.imageSize
    new_tex.SampleCount = 1
    new_tex.ReadTextureLayout = tex_.readTexLayout
    new_tex.sparseBinding = tex_.sparseBinding
    new_tex.sparseResidency = tex_.sparseResidency
    new_tex.BlockHeightLog2 = tex_.blockHeightLog2
    new_tex.textureLayout = 0 if not tex_.readTexLayout else (tex_.sparseResidency << 5 | tex_.sparseBinding << 4 | tex_.blockHeightLog2)
    new_tex.textureLayout2 = 65543
    new_tex.AccessFlags = enum("Syroot.NintenTools.NSW.Bntx.GFX.AccessFlags", 32)
    new_tex.ArrayLength = 1
    new_tex.Regs = None
    new_tex.UserDataDict = ResDict()
    new_tex.UserData = List[UserData]()

    bntx_file = BntxFile(System.IO.MemoryStream(bntx.read_bytes()), False)
    bntx_file.Textures.Add(new_tex)
    bntx_file.Save(str(bntx))
    return True


def _adapt_flim_for_target(flim, target_tex):
    adapted = _deswizzle_flim_payload(flim)
    raw = adapted.data

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
    if not o_tex:
        return _append_new_texture_entry(bntx, bflim, flim)
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