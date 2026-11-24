"""Builds ChatVoice Local.exe with PyInstaller, then checks the result. Run through build_exe.bat on Windows."""
import importlib.metadata as metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time

import PyInstaller.__main__ as pyinstaller

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
NAME = 'ChatVoice Local'
TARGET = os.path.join(ROOT, 'dist', NAME)
debug = 'debug' in sys.argv

with open('main.py', encoding='utf-8') as fh:
    VERSION = re.search(r"APP_VERSION = '([^']+)'", fh.read()).group(1)
VERSION_TUPLE = (tuple(int(part) for part in re.findall(r'\d+', VERSION)) + (0, 0, 0, 0))[:4]

# Packages that ship data files or load plugins at run time. Only the ones installed here are included.
COLLECT_ALL = ['kokoro', 'misaki', 'espeakng_loader', 'phonemizer', 'language_tags', 'spacy', 'en_core_web_sm',
               'jieba', 'pypinyin', 'pypinyin_dict', 'cn2an', 'num2words', 'pyopenjtalk', 'jaconv', 'mojimoji',
               'fugashi', 'unidic_lite', 'keyring', 'win32ctypes', '_sounddevice_data', '_soundfile_data']
# transformers loads its model files lazily, so PyInstaller cannot see that Kokoro needs the Albert ones.
COLLECT_SUBMODULES = ['transformers.models.albert']
# Packages that read their own version at import time.
COPY_METADATA = ['transformers', 'tokenizers', 'huggingface_hub', 'safetensors', 'regex', 'requests', 'packaging',
                 'filelock', 'numpy', 'tqdm', 'torch', 'keyring', 'spacy', 'en_core_web_sm', 'pyyaml']
HIDDEN = ['keyring.backends.Windows', 'win32ctypes.pywin32.win32cred', 'sounddevice', 'soundfile', 'websockets', 'PIL._tkinter_finder']


def remove_old_build():
    # An exe that is still running (or being scanned by antivirus) cannot be deleted, so wait and retry.
    for attempt in range(6):
        try:
            if os.path.isdir(TARGET): shutil.rmtree(TARGET)
            return
        except OSError:
            time.sleep(2)
    print()
    print(f'Could not delete the old build in "{TARGET}".')
    print('Close ChatVoice Local (check Task Manager for "ChatVoice Local.exe"), pause your antivirus')
    print('scan of this folder if it is running, and run the build again.')
    sys.exit(1)


def write_version_files():
    parts = ', '.join(str(part) for part in VERSION_TUPLE)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({parts}), prodvers=({parts}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{NAME}'), StringStruct('FileDescription', '{NAME}'),
      StringStruct('FileVersion', '{VERSION}'), StringStruct('InternalName', '{NAME}'),
      StringStruct('OriginalFilename', '{NAME}.exe'), StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ])
"""
    with open('version_info.txt', 'w', encoding='utf-8') as fh: fh.write(text)
    with open('installer_version.iss', 'w', encoding='utf-8') as fh: fh.write(f'#define MyAppVersion "{VERSION}"\n')


remove_old_build()
write_version_files()
args = ['main.py', '--name', NAME, '--noconfirm', '--clean', '--onedir', '--noupx',
        '--console' if debug else '--windowed', '--icon', 'icon.ico', '--version-file', 'version_info.txt']
for package in COLLECT_ALL:
    if importlib.util.find_spec(package): args += ['--collect-all', package]
    else: print('Skipping (not installed):', package)
for module in COLLECT_SUBMODULES:
    try:
        if importlib.util.find_spec(module): args += ['--collect-submodules', module]
    except ModuleNotFoundError: pass
for package in COPY_METADATA:
    try: metadata.distribution(package)
    except metadata.PackageNotFoundError: continue
    args += ['--copy-metadata', package]
for module in HIDDEN: args += ['--hidden-import', module]

pyinstaller.run(args)

# Helper files sit next to the .exe so the app can find and run them.
for name in ('windows_sapi_speech.ps1', 'qwen3tts_worker.py', 'qwen3tts_train.py', 'setup_voice_clone.ps1', 'README.md', 'icon.ico', 'icon.png'):
    shutil.copy2(os.path.join(ROOT, name), TARGET)

exe = os.path.join(TARGET, NAME + '.exe')
print()
print('Checking the new build (this can take a minute)...')
result_file = os.path.join(TARGET, 'selftest_result.txt')
if os.path.exists(result_file): os.remove(result_file)
try:
    subprocess.run([exe, '--selftest'], timeout=600)
except subprocess.TimeoutExpired:
    print('The check did not finish in 10 minutes.')
if os.path.exists(result_file):
    with open(result_file, encoding='utf-8') as fh: report = fh.read()
    os.remove(result_file)
    print(report)
    passed = 'SELFTEST PASSED' in report
else:
    print('The check did not run. Open the log next to the .exe for details: chatvoice_local.log')
    passed = False
print('Build finished:' if passed else 'Build finished, but the check found problems:', exe)
print('Next: run the .exe to try it. To make an installer, run build_installer.bat.')
sys.exit(0 if passed else 1)
