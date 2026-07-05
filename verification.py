import ctypes
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from keystone import Ks, KS_ARCH_X86, KS_MODE_16, KS_MODE_32, KS_MODE_64


def _install_qemu_windows_portable(verbose=True):
    """
    Downloads the official QEMU Windows installer (the same builds
    linked from https://www.qemu.org/download/#windows, hosted at
    qemu.weilnetz.de) and runs it silently (/S) into a user-local
    folder under %LOCALAPPDATA% -- no admin rights, no install
    wizard, no clicking anything.

    NOTE: this path is implemented carefully but has not been run
    against a real Windows machine -- verify it on yours and report
    back if the installer's silent-mode flags behave differently
    than documented for the version you get.
    """
    import re

    install_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
        "asmlib_qemu", "qemu",
    )
    exe_path = os.path.join(install_dir, "qemu-system-x86_64.exe")
    if os.path.exists(exe_path):
        return exe_path

    def _log(msg):
        if verbose:
            print(f"[ensure_qemu_installed] {msg}")

    _log("locating the latest official QEMU Windows build...")
    listing_url = "https://qemu.weilnetz.de/w64/"
    try:
        with urllib.request.urlopen(listing_url, timeout=15) as resp:
            html = resp.read().decode(errors="ignore")
    except Exception as e:
        raise RuntimeError(
            f"Could not reach {listing_url} to find a QEMU build "
            f"({e}). Download and install QEMU manually from "
            f"https://www.qemu.org/download/#windows, then try again."
        )

    candidates = sorted(set(re.findall(r'href="(qemu-w64-setup-[^"]+\.exe)"', html)))
    if not candidates:
        raise RuntimeError(
            f"Found the page at {listing_url} but no installer link "
            f"matched the expected pattern -- the site layout may have "
            f"changed. Download QEMU manually from "
            f"https://www.qemu.org/download/#windows"
        )
    installer_name = candidates[-1]  # filenames are date-stamped -> lexicographic sort = latest
    installer_url = listing_url + installer_name

    _log(f"downloading {installer_name} ...")
    tmp_installer = os.path.join(tempfile.gettempdir(), installer_name)
    try:
        urllib.request.urlretrieve(installer_url, tmp_installer)
    except Exception as e:
        raise RuntimeError(f"Download of {installer_url} failed: {e}")

    os.makedirs(install_dir, exist_ok=True)
    # NSIS silent-install flags: /S = silent, /D=<dir> = install directory.
    # /D must be the LAST argument and must not be quoted, even if the
    # path contains spaces (an NSIS requirement, not a Python one).
    install_args = ["/S", f"/D={install_dir}"]

    _log(f"installing silently to {install_dir}...")
    try:
        subprocess.run([tmp_installer] + install_args, capture_output=True, timeout=180)
    except OSError as e:
        # WinError 740 = "The requested operation requires elevation".
        # The official QEMU installer's manifest requires admin no
        # matter which folder it targets -- subprocess.run can't grant
        # that. ShellExecuteW with the "runas" verb triggers the one
        # UAC prompt Windows actually requires here; after you click
        # "Yes" once, /S still keeps everything else silent.
        if getattr(e, "winerror", None) == 740:
            _log(
                "the installer needs admin rights -- a UAC prompt should "
                "appear now. Click \"Yes\" to continue (this is the only "
                "prompt you'll see)."
            )
            import ctypes as _ctypes
            _ctypes.windll.shell32.ShellExecuteW(
                None, "runas", tmp_installer, " ".join(install_args), None, 1
            )
            # ShellExecuteW doesn't block, so poll for the install to finish
            waited = 0
            while not os.path.exists(exe_path) and waited < 120:
                import time as _time
                _time.sleep(2)
                waited += 2
        else:
            raise

    if not os.path.exists(exe_path):
        raise RuntimeError(
            f"Installer ran but {exe_path} wasn't created. Either the "
            f"UAC prompt wasn't accepted, or this build's silent flags "
            f"or directory layout differ -- try running "
            f"'{tmp_installer}' manually to install with the UI, "
            f"or install via https://www.qemu.org/download/#windows"
        )

    _log(f"QEMU ready at {exe_path}")
    return exe_path


def ensure_qemu_installed(qemu_binary="qemu-system-x86_64", auto_install=False, verbose=True):
    """
    Check whether a QEMU x86-64 system emulator is available on PATH.

    QEMU is treated as an optional runtime dependency. By default this
    function does not install anything automatically; it only raises a
    clear error that tells the user how to opt in explicitly.

    If auto_install is True, it tries to install QEMU using whatever
    package manager fits the current platform:

      - Linux:   apt, dnf, or pacman (whichever is present), via sudo
      - macOS:   Homebrew (brew)
      - Windows: winget, falling back to a manual download link

    Returns the resolved path to the qemu-system-x86_64 binary.
    Raises RuntimeError with manual install instructions if it can't
    find or install QEMU automatically.
    """
    existing = shutil.which(qemu_binary)
    if existing:
        return existing

    if not auto_install:
        raise RuntimeError(
            f"{qemu_binary} not found on PATH. QEMU is an optional dependency "
            f"for this package, so installation is disabled by default. "
            f"Install it manually or run: python -m Pyliu install-qemu"
        )

    system = platform.system()

    def _log(msg):
        if verbose:
            print(f"[ensure_qemu_installed] {msg}")

    def _try(cmd, timeout=None):
        _log(f"running: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            _log(
                f"'{cmd[0]}' didn't finish within {timeout}s -- it's likely "
                f"waiting on a UAC/consent dialog that isn't visible to this "
                f"script. Giving up on this method and trying the next one."
            )
            return False

    def _maybe_sudo(cmd):
        # root (e.g. inside containers) has no sudo binary and doesn't
        # need one; everyone else does
        needs_privilege = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0
        if needs_privilege and shutil.which("sudo"):
            return ["sudo"] + cmd
        return cmd

    if system == "Linux":
        if shutil.which("apt-get"):
            _log("detected apt -- installing qemu-system-x86")
            # apt-get update can return nonzero due to unrelated broken
            # third-party repos even when the repos we need are fine,
            # so don't gate the install attempt on its exit code --
            # only the final "is the binary on PATH" check matters.
            _try(_maybe_sudo(["apt-get", "update"]), timeout=120)
            ok = _try(_maybe_sudo(["apt-get", "install", "-y", "qemu-system-x86"]), timeout=180)
        elif shutil.which("dnf"):
            _log("detected dnf -- installing qemu-system-x86")
            ok = _try(_maybe_sudo(["dnf", "install", "-y", "qemu-system-x86"]), timeout=180)
        elif shutil.which("pacman"):
            _log("detected pacman -- installing qemu")
            ok = _try(_maybe_sudo(["pacman", "-Sy", "--noconfirm", "qemu-system-x86"]), timeout=180)
        else:
            raise RuntimeError(
                "No supported package manager found (apt/dnf/pacman). "
                "Install QEMU manually: https://www.qemu.org/download/#linux"
            )

    elif system == "Darwin":
        if not shutil.which("brew"):
            raise RuntimeError(
                "Homebrew not found. Install it from https://brew.sh, "
                "then run: brew install qemu"
            )
        _log("detected Homebrew -- installing qemu")
        ok = _try(["brew", "install", "qemu"], timeout=300)

    elif system == "Windows":
        raise RuntimeError(
            "QEMU not found. Install manually or run: python -m Pyliu install-qemu"
        )

    else:
        raise RuntimeError(f"Unsupported platform for auto-install: {system}")

    resolved = shutil.which(qemu_binary)
    if not resolved:
        raise RuntimeError(
            f"Install attempted but {qemu_binary} still isn't on PATH. "
            f"You may need to open a new shell/terminal for PATH changes "
            f"to take effect, or install QEMU manually: "
            f"https://www.qemu.org/download/"
        )


def install_qemu(qemu_binary="qemu-system-x86_64", verbose=True):
    """Install QEMU explicitly when the user requests it."""
    return ensure_qemu_installed(qemu_binary=qemu_binary, auto_install=True, verbose=verbose)


def main(argv=None):
    """Small CLI entry point for optional QEMU installation."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m Pyliu")
    subparsers = parser.add_subparsers(dest="command")

    install_parser = subparsers.add_parser("install-qemu", help="Install QEMU explicitly")
    install_parser.add_argument("--qemu-binary", default="qemu-system-x86_64")
    install_parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "install-qemu":
        install_qemu(qemu_binary=args.qemu_binary, verbose=args.verbose)
        print("QEMU installation completed or was accepted by the runtime.")
        return 0

    parser.print_help()
    return 0


class CPU:
    def __init__(self):
        self.instructions = []

    def emit(self, instruction):
        self.instructions.append(instruction)

    def _mem_operand(self, operand):
        operand = str(operand).strip()
        if operand.startswith("[") and operand.endswith("]"):
            return operand
        return f"[{operand}]"

    def mov(self, dest, src):
        self.emit(f"mov {dest}, {src}")

    def lea(self, dest, src):
        self.emit(f"lea {dest}, {src}")

    def xchg(self, a, b):
        self.emit(f"xchg {a}, {b}")

    def movzx(self, dest, src):
        self.emit(f"movzx {dest}, {src}")

    def movsx(self, dest, src):
        self.emit(f"movsx {dest}, {src}")

    def add(self, dest, src):
        self.emit(f"add {dest}, {src}")

    def sub(self, dest, src):
        self.emit(f"sub {dest}, {src}")

    def inc(self, dest):
        self.emit(f"inc {dest}")

    def dec(self, dest):
        self.emit(f"dec {dest}")

    def imul(self, dest, src):
        self.emit(f"imul {dest}, {src}")

    def idiv(self, src):
        self.emit(f"idiv {src}")

    def neg(self, dest):
        self.emit(f"neg {dest}")

    def and_(self, dest, src):
        self.emit(f"and {dest}, {src}")

    def or_(self, dest, src):
        self.emit(f"or {dest}, {src}")

    def xor(self, dest, src):
        self.emit(f"xor {dest}, {src}")

    def not_(self, dest):
        self.emit(f"not {dest}")

    def test(self, a, b):
        self.emit(f"test {a}, {b}")

    def shl(self, dest, count):
        self.emit(f"shl {dest}, {count}")

    def shr(self, dest, count):
        self.emit(f"shr {dest}, {count}")

    def sar(self, dest, count):
        self.emit(f"sar {dest}, {count}")

    def rol(self, dest, count):
        self.emit(f"rol {dest}, {count}")

    def ror(self, dest, count):
        self.emit(f"ror {dest}, {count}")

    def cmp(self, a, b):
        self.emit(f"cmp {a}, {b}")

    def jmp(self, target):
        self.emit(f"jmp {target}")

    def je(self, target):
        self.emit(f"je {target}")

    def jz(self, target):
        self.emit(f"jz {target}")

    def jne(self, target):
        self.emit(f"jne {target}")

    def jnz(self, target):
        self.emit(f"jnz {target}")

    def jg(self, target):
        self.emit(f"jg {target}")

    def jge(self, target):
        self.emit(f"jge {target}")

    def jl(self, target):
        self.emit(f"jl {target}")

    def jle(self, target):
        self.emit(f"jle {target}")

    def ja(self, target):
        self.emit(f"ja {target}")

    def jb(self, target):
        self.emit(f"jb {target}")

    def call(self, target):
        self.emit(f"call {target}")

    def ret(self):
        self.emit("ret")

    def push(self, src):
        self.emit(f"push {src}")

    def pop(self, dest):
        self.emit(f"pop {dest}")

    def syscall(self):
        self.emit("syscall")

    def int_(self, value):
        self.emit(f"int {value}")

    def nop(self):
        self.emit("nop")

    def hlt(self):
        self.emit("hlt")

    def cpuid(self):
        self.emit("cpuid")

    def rdtsc(self):
        self.emit("rdtsc")

    def clc(self):
        self.emit("clc")

    def stc(self):
        self.emit("stc")

    def cmc(self):
        self.emit("cmc")

    def cli(self):
        self.emit("cli")

    def sti(self):
        self.emit("sti")

    def load(self, dest, addr):
        self.emit(f"mov {dest}, {self._mem_operand(addr)}")

    def store(self, addr, src):
        self.emit(f"mov {self._mem_operand(addr)}, {src}")

    def deref(self, dest, src):
        self.emit(f"mov {dest}, {self._mem_operand(src)}")

    def addr(self, dest, src):
        self.emit(f"lea {dest}, {self._mem_operand(src)}")

    def label(self, name):
        self.emit(f"{name}:")

    def goto(self, target):
        self.emit(f"jmp {target}")

    def if_true(self, reg, target):
        self.emit(f"test {reg}, {reg}")
        self.emit(f"jnz {target}")

    def if_false(self, reg, target):
        self.emit(f"test {reg}, {reg}")
        self.emit(f"jz {target}")

    def loop(self, label_name):
        self.emit(f"jmp {label_name}")

    # --- bare-metal helpers (used by QEMURuntime) ---

    def out_serial_char(self, char_reg="al"):
        """Write one byte (from the given 8-bit register) to the
        standard PC serial port (COM1, 0x3F8), so QEMU's
        -serial stdio can capture it as real program output."""
        self.emit("mov dx, 0x3f8")
        self.emit(f"out dx, {char_reg}")

    def halt_forever(self):
        """Stop execution cleanly instead of falling into whatever
        garbage bytes come after the program in memory."""
        self.emit("cli")
        self.label("__halt_loop")
        self.emit("hlt")
        self.emit("jmp __halt_loop")


class Memory:
    def __init__(self):
        self.vars = {}
        self.stack_offset = 0
        self.counter = 0
        self.frame_size = 0
        self.free_slots = []

    def _align(self, value, alignment=8):
        return ((value + alignment - 1) // alignment) * alignment

    def alloc(self, name, size=8):
        if name in self.vars:
            return self.vars[name]["address"]

        size = max(int(size), 8)
        for idx, (slot_offset, slot_size) in enumerate(self.free_slots):
            if slot_size >= size:
                self.free_slots.pop(idx)
                offset = slot_offset
                self.vars[name] = {"address": f"[rbp-{offset}]", "size": size, "offset": offset}
                self.frame_size = max(self.frame_size, offset)
                return self.vars[name]["address"]

        self.stack_offset = self._align(self.stack_offset + size, 8)
        offset = self.stack_offset
        self.frame_size = max(self.frame_size, offset)
        self.vars[name] = {"address": f"[rbp-{offset}]", "size": size, "offset": offset}
        return self.vars[name]["address"]

    def free(self, name):
        entry = self.vars.pop(name, None)
        if entry is not None:
            self.free_slots.append((entry["offset"], entry["size"]))

    def get(self, name):
        return self.vars[name]["address"]

    def string(self, value):
        return str(value)

    def array(self, name, values):
        return [self.alloc(f"{name}_{i}") for i in range(len(values))]

    def pointer(self, name, target):
        return self.alloc(name)

    def struct(self, name, fields):
        return {field: self.alloc(f"{name}_{field}") for field in fields}

    def snapshot(self):
        return {k: v["address"] for k, v in self.vars.items()}


class Compiler:
    """Assembles for the JIT (VirtualAlloc) path. Defaults to 64-bit,
    matching the in-process Runtime below."""

    def __init__(self, mode=KS_MODE_64):
        self.ks = Ks(KS_ARCH_X86, mode)
        self.last_asm = ""
        self.last_machine_code = b""

    def _validate(self, program):
        for instruction in program.cpu.instructions:
            if not instruction or instruction.endswith(":"):
                continue
            if instruction.startswith(("mov", "add", "sub", "cmp", "test", "and", "or", "xor", "shl", "shr", "sar", "rol", "ror")):
                parts = [p.strip() for p in instruction.split(" ", 1)[1].split(",")]
                if len(parts) != 2:
                    raise ValueError(f"Invalid operands for {instruction}")
                left, right = parts
                if left.startswith("[") and left.endswith("]"):
                    left = left[1:-1]
                if right.startswith("[") and right.endswith("]"):
                    right = right[1:-1]
                if left in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}:
                    raise ValueError(f"Invalid destination operand for {instruction}")
                if right in {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"} and left.startswith(("[", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9")):
                    raise ValueError(f"Invalid immediate operand for {instruction}")

    def compile(self, program):
        self._validate(program)
        self.last_asm = "\n".join(program.cpu.instructions)
        encoding, _ = self.ks.asm(self.last_asm)
        self.last_machine_code = bytes(encoding)
        program.asm = self.last_asm
        program.machine_code = self.last_machine_code
        return self.last_machine_code


class Runtime:
    """In-process JIT execution via VirtualAlloc (Windows only)."""

    def __init__(self):
        self.kernel32 = ctypes.windll.kernel32
        self.kernel32.VirtualAlloc.restype = ctypes.c_void_p
        self.MEM_COMMIT = 0x1000
        self.MEM_RESERVE = 0x2000
        self.PAGE_EXECUTE_READWRITE = 0x40

    def execute(self, machine_code):
        addr = self.kernel32.VirtualAlloc(
            None,
            len(machine_code),
            self.MEM_COMMIT | self.MEM_RESERVE,
            self.PAGE_EXECUTE_READWRITE,
        )
        ctypes.memmove(addr, machine_code, len(machine_code))
        func = ctypes.CFUNCTYPE(ctypes.c_int)(addr)
        return func()

    def run(self, program):
        program.compile()
        return self.execute(program.machine_code)


class QEMURuntime:
    """
    Boots machine code as a real bare-metal boot sector inside an
    isolated QEMU virtual machine. No OS, no bootloader, no disk
    image beyond the single 512-byte sector this class builds.

    A crash, bad memory access, or infinite loop in your code is
    contained entirely inside the QEMU subprocess -- your actual
    Python process is never touched, unlike the in-process
    VirtualAlloc Runtime above.

    Runs your code in 32-bit protected mode by default. A fixed
    16-bit real-mode stub handles the switch into protected mode
    before jumping into your compiled bytes.

    LIMITATION: this does not set up long mode (64-bit). Full
    64-bit support needs paging (page tables + PAE + EFER.LME +
    CR0.PG), which is real follow-up infrastructure, not a small
    addition. Assemble your program body in 32-bit mode for this
    runtime (use QEMURuntime.compile_32, not the 64-bit Compiler
    above).

    Output: there's no return value the way a JIT'd function has
    one. Instead, write bytes to the serial port with
    CPU.out_serial_char() -- QEMU's -serial stdio captures it as
    real captured output from the guest machine.
    """

    ORIGIN = 0x7C00
    SECTOR_SIZE = 512

    def __init__(self, timeout=5, qemu_binary="qemu-system-x86_64", auto_install=False):
        self.timeout = timeout
        self.qemu_binary = ensure_qemu_installed(qemu_binary, auto_install=auto_install)
        self.last_output = ""
        self.last_image_path = None

    # ---- assembling the user's program body ----

    def compile_32(self, program):
        ks = Ks(KS_ARCH_X86, KS_MODE_32)
        asm_text = "\n".join(program.cpu.instructions)
        encoding, _ = ks.asm(asm_text)
        machine_code = bytes(encoding)
        program.asm = asm_text
        program.machine_code = machine_code
        return machine_code

    # ---- GDT, built as raw bytes rather than via the assembler,
    # since Keystone's directive/data-table support (dq/db, label
    # arithmetic like `gdt_end - gdt_start`) isn't reliable enough
    # to trust for a boot sector ----

    def _build_flat_gdt(self):
        null_entry = struct.pack("<Q", 0)
        # base=0, limit=0xFFFFF, 4KB granularity, 32-bit, present, ring0
        code_entry = struct.pack("<HHBBBB", 0xFFFF, 0x0000, 0x00, 0b10011010, 0b11001111, 0x00)
        data_entry = struct.pack("<HHBBBB", 0xFFFF, 0x0000, 0x00, 0b10010010, 0b11001111, 0x00)
        gdt = null_entry + code_entry + data_entry
        return gdt, len(gdt) - 1

    def _assemble_real_stub(self, gdt_descriptor_offset, pm_entry_offset):
        ks16 = Ks(KS_ARCH_X86, KS_MODE_16)
        asm = f"""
        cli
        xor ax, ax
        mov ds, ax
        mov es, ax
        mov ss, ax
        mov sp, 0x7c00
        lgdt [0x7c00 + {gdt_descriptor_offset}]
        mov eax, cr0
        or eax, 1
        mov cr0, eax
        ljmp 0x08:0x7c00 + {pm_entry_offset}
        """
        encoding, _ = ks16.asm(asm)
        return bytes(encoding)

    def _assemble_pm_entry(self):
        ks32 = Ks(KS_ARCH_X86, KS_MODE_32)
        asm = """
        mov ax, 0x10
        mov ds, ax
        mov es, ax
        mov fs, ax
        mov gs, ax
        mov ss, ax
        mov esp, 0x90000
        """
        encoding, _ = ks32.asm(asm)
        return bytes(encoding)

    def build_boot_sector(self, user_code):
        """
        Lays out one 512-byte sector:
          [0x7C00]     16-bit real-mode stub -> switches to protected mode
          [...]        flat GDT (3 entries: null, code, data)
          [...]        GDT descriptor (limit + linear base address)
          [...]        32-bit protected-mode entry stub
          [...]        your compiled 32-bit user code
          [0x7DFE]     boot signature 0x55AA
        """
        # pass 1: assemble the real-mode stub with placeholder offsets,
        # purely to measure its length (Keystone has no multi-pass
        # linker, so offsets are computed by hand here)
        stub_probe = self._assemble_real_stub(0, 0)
        stub_len = len(stub_probe)

        gdt_bytes, gdt_limit = self._build_flat_gdt()
        gdt_offset = stub_len
        gdt_descriptor_offset = gdt_offset + len(gdt_bytes)
        gdt_descriptor = struct.pack("<HI", gdt_limit, self.ORIGIN + gdt_offset)
        pm_entry_offset = gdt_descriptor_offset + len(gdt_descriptor)

        # pass 2: reassemble the stub now that real offsets are known
        real_stub = self._assemble_real_stub(gdt_descriptor_offset, pm_entry_offset)
        if len(real_stub) != stub_len:
            raise RuntimeError(
                "Real-mode stub changed size between assembly passes "
                "(offset-dependent encoding shifted length) -- offsets "
                "are no longer valid. This is an internal bug in "
                "build_boot_sector, not your program."
            )

        pm_entry = self._assemble_pm_entry()

        sector = bytearray(self.SECTOR_SIZE)
        sector[0:len(real_stub)] = real_stub
        sector[gdt_offset:gdt_offset + len(gdt_bytes)] = gdt_bytes
        sector[gdt_descriptor_offset:gdt_descriptor_offset + len(gdt_descriptor)] = gdt_descriptor
        sector[pm_entry_offset:pm_entry_offset + len(pm_entry)] = pm_entry

        code_offset = pm_entry_offset + len(pm_entry)
        end_offset = code_offset + len(user_code)
        if end_offset > self.SECTOR_SIZE - 2:
            raise ValueError(
                f"Boot sector overflow: stub + GDT + entry + your code = "
                f"{end_offset} bytes, max is {self.SECTOR_SIZE - 2}. "
                f"Multi-sector loading isn't implemented yet -- shrink "
                f"the program or ask to add sector-spanning support."
            )

        sector[code_offset:code_offset + len(user_code)] = user_code
        sector[self.SECTOR_SIZE - 2:self.SECTOR_SIZE] = b"\x55\xAA"
        return bytes(sector)

    # ---- execution ----

    def execute(self, machine_code):
        image = self.build_boot_sector(machine_code)

        tmp = tempfile.NamedTemporaryFile(suffix=".img", delete=False)
        tmp.write(image)
        tmp.close()
        self.last_image_path = tmp.name

        cmd = [
            self.qemu_binary,
            "-drive", f"format=raw,file={tmp.name}",
            "-nographic",
            "-serial", "stdio",
            "-no-reboot",
            "-display", "none",
            "-monitor", "none",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=self.timeout, text=True
            )
            self.last_output = result.stdout
        except subprocess.TimeoutExpired as e:
            # Expected for a program that halts/loops forever (e.g. via
            # halt_forever()) rather than triggering a triple-fault --
            # QEMU just keeps running until this timeout kills it.
            out = e.stdout
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            self.last_output = out or ""
        return self.last_output

    def run(self, program):
        machine_code = self.compile_32(program)
        return self.execute(machine_code)


class Program:
    def __init__(self, cpu=None, memory=None, compiler=None, runtime=None):
        self.cpu = cpu or CPU()
        self.memory = memory or Memory()
        self.compiler = compiler or Compiler()
        # lazy: Runtime() touches ctypes.windll (Windows-only), so it
        # isn't constructed until .run() is actually called -- this
        # keeps run_on_qemu() usable cross-platform without ever
        # needing the JIT runtime at all.
        self._runtime_override = runtime
        self._runtime = None
        self.asm = ""
        self.machine_code = b""
        self.result = None

    def reset(self):
        self.cpu.instructions = []
        self.asm = ""
        self.machine_code = b""
        self.result = None

    def _ensure_frame(self):
        frame_size = max(0, self.memory.frame_size)
        if frame_size <= 0:
            return
        aligned = max(0x20, ((frame_size + 15) // 16) * 16)
        if any(instr.startswith("sub rsp") for instr in self.cpu.instructions):
            return
        if len(self.cpu.instructions) >= 2 and self.cpu.instructions[1] == "mov rbp, rsp":
            self.cpu.instructions.insert(2, f"sub rsp, {aligned}")

    def begin(self):
        self.reset()
        self.cpu.push("rbp")
        self.cpu.mov("rbp", "rsp")

    def end(self):
        self.cpu.mov("rsp", "rbp")
        self.cpu.pop("rbp")
        self.cpu.ret()

    def compile(self):
        self._ensure_frame()
        self.compiler.compile(self)
        return self.machine_code

    @property
    def runtime(self):
        if self._runtime is None:
            self._runtime = self._runtime_override or Runtime()
        return self._runtime

    def run(self):
        self.result = self.runtime.run(self)
        return self.result

    def run_on_qemu(self, timeout=5):
        """Run this program's instructions on a bare-metal QEMU VM
        instead of the default in-process JIT runtime. Program bodies
        for this path should be written in 32-bit terms (eax/ebx/...
        registers), since QEMURuntime assembles in 32-bit mode."""
        qemu_runtime = QEMURuntime(timeout=timeout)
        self.result = qemu_runtime.run(self)
        self.machine_code = getattr(self, "machine_code", b"")
        return self.result

    def __getattr__(self, name):
        if hasattr(self.cpu, name):
            return getattr(self.cpu, name)
        if hasattr(self.memory, name):
            return getattr(self.memory, name)
        raise AttributeError(name)


class OS:
    def boot(self, program):
        return program.run()


class Playground:
    def __init__(self):
        self.program = Program()

    def run(self):
        return self.program.run()


class Debugger:
    def inspect(self, program):
        return {
            "asm": program.asm,
            "machine_code": program.machine_code.hex(),
            "memory": program.memory.snapshot(),
            "result": program.result,
        }