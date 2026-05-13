# Ultimate BotW Converter
A script combining various sources to convert BotW WiiU mods for the Switch version of the game

## Requirements
- [Python 3.9+](https://www.python.org/downloads/release/python-3913/) (You must check `Add Python to PATH` during installation on Windows)

- [cobra838/BCML fork](https://github.com/cobra838/BCML). Install for Python 3.9:  
`
py -3.9 -m pip install --force-reinstall "https://raw.githubusercontent.com/cobra838/BCML/master/target/wheels/bcml-3.10.8-cp39-none-win_amd64.whl"
`

- [.NET 5.0 Runtime](https://dotnet.microsoft.com/en-us/download/dotnet/5.0/runtime) (required for the [HKX2 ReadWrite Havok converter](https://gitlab.com/HKX2))

- A legal, unpacked dump of BoTW Switch (1.6.0) for BCML. For obtaining a BoTW dump, see https://zeldamods.org/wiki/Help:Dumping_games.

## Usage

You can run the converter module directly:
`python -m ubotw_converter.converter -s "your.bnp" --log-level debug`

- `-s` runs the converter in single-threaded mode, which can help avoid multiprocessing-related issues.

- `--log-level debug` currently enables additional validation for converted BFRES files. This is slower, but more reliable for troubleshooting problematic BFRES conversions.

On Windows, you can also use:
- `ubotw_converter\convert.bat "your.bnp"`
- `ubotw_converter\convert_single_thread.bat "your.bnp"`

You can also drag and drop one or more `.bnp` files onto those `.bat` files.

On systems with `bash`, you can use:
- `./ubotw_converter/convert.sh your.bnp`

## Supported formats
BCML's converter is still limited, so using other tools to convert those files that it can't is our only option for now. With this script, I've automated the process of using those other tools and added these formats to the supported list:
- `.bars`
- `.bfstm`
- `.sbfres`
- `.sbitemico`
- `.hkcl`
- `.hkrg`
- `.hkrb`
- `.shknm2`
- `.shksc`
- `.shktmrb`
- `.bflim`
- `.bflan`
- `.sbeco`
- `.bcamanim`*
- `.bflyt`**
- `.sesetlist`***

\*`.bcamanim` files are not converted reliably yet. The converter currently attempts direct BFRES platform conversion for them, but tested outputs do not always match stock Switch files, so manual replacement or further format-specific handling may still be required.

\*\*`.bflyt` conversion aims for 1:1 output in almost all cases.  
Known exceptions:  
`AppMap_00`, `MainHardMode_00`, `MainShortCut_00`, `PaTempMeter_00`, `PauseMenuBG_00`, `ShopBtnList5_00`, `SystemWindow_00`.  
Also exceptions:  
`ChangeControllerNN_00` (Switch-only)  
`PaAllControllerTipsNN_00` (Switch) - `PaAllControllerTips_00` (WiiU)  
`PaMessageTipsDrcImageNN_00` (Switch)  - `PaMessageTipsDrcImage_00` (WiiU)  
`PaMessageTipsDrcImgAmiiboNN_00` (Switch) - `PaMessageTipsDrcImgAmiibo_00` (WiiU)

\*\*\*`.sesetlist` conversion is partial. The converter uses the stock Switch file as the base and patches matching EMTR parameter blocks by name using the WiiU mod vs stock WiiU diff. Texture/shader data remains from the stock Switch file; unknown EMTRs or chunks with unknown WiiU -> Switch mapping are left unchanged and logged as warnings.


## Installation
For now, you can install the prerelease by running `pip install ubotw-converter` from a Command-Line Interface (CLI).

If wanting to install from source, run `pip install -e .` inside the folder where the source code is located 

In a CLI, run `convert_to_switch path/to/your/bnp`, and the conversion process will start. If you encounter problems caused by multi-processing, you can use `convert_to_switch -s path/to/your/bnp` to enable single core. 


## Credits 
- [AboodXD](https://github.com/aboood40091) - BCFSTM-BCFWAV Converter, BNTX Injector, Bflim Extractor, LayoutExporterU
- [NanobotZ](https://github.com/NanobotZ) - bfstpfixer.py
- [SamusAranX](https://github.com/SamusAranX) - Original bars_extractor.py script
- [Aaaboy97](https://github.com/Aaaboy97) - Bars repacker script
- [KillzXGaming](https://github.com/KillzXGaming) - BfresPlatformConverter, BfresLibrary
- [krenyy](https://gitlab.com/krenyy) - HKXConvert, [HKX2](https://gitlab.com/HKX2) (HKX2Library, ReadWrite)
- [NiceneNerd](https://github.com/NiceneNerd) - BOTW Cross-Platform Mod Loader
- [Leoetlino](https://github.com/leoetlino) - All his tools for working with BotW files
- The creators of [Pythonnet](https://github.com/pythonnet)
- [HGStone](https://github.com/HGStone) - Bat script and testing
