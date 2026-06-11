"""COMPAC 串口底层读写（Linux termios；Windows/macOS pyserial；memory:// 内存模拟）。"""
from __future__ import annotations

import os
import sys
from abc import ABC, abstractmethod
from typing import Optional


class SerialPortError(OSError):
    pass


class SerialPortBase(ABC):
    @abstractmethod
    def open(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def is_open(self) -> bool:
        ...

    @abstractmethod
    def write(self, data: bytes) -> int:
        ...

    @abstractmethod
    def read(self, max_bytes: int = 4096) -> bytes:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


def _parity_const(parity: str) -> int:
    import termios

    p = (parity or "N").upper()
    if p in ("N", "NONE"):
        return termios.PARITY_NONE
    if p in ("E", "EVEN"):
        return termios.PARITY_EVEN
    if p in ("O", "ODD"):
        return termios.PARITY_ODD
    raise SerialPortError(f"unsupported parity: {parity!r}")


class TermiosSerialPort(SerialPortBase):
    """Linux/Unix 串口（termios + 非阻塞读）。"""

    def __init__(
        self,
        device: str,
        *,
        baud_rate: int = 9600,
        data_bits: int = 8,
        parity: str = "N",
        stop_bits: int = 1,
    ) -> None:
        self._device = device
        self._baud = int(baud_rate)
        self._data_bits = int(data_bits)
        self._parity = parity
        self._stop_bits = int(stop_bits)
        self._fd: Optional[int] = None

    @property
    def name(self) -> str:
        return self._device

    def is_open(self) -> bool:
        return self._fd is not None

    def open(self) -> None:
        if self._fd is not None:
            return
        import fcntl
        import termios

        try:
            fd = os.open(self._device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            raise SerialPortError(f"cannot open {self._device}: {e}") from e

        speed_map = {
            9600: termios.B9600,
            19200: termios.B19200,
            38400: termios.B38400,
            57600: termios.B57600,
            115200: termios.B115200,
        }
        speed = speed_map.get(self._baud)
        if speed is None:
            os.close(fd)
            raise SerialPortError(f"unsupported baud rate: {self._baud}")

        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 if self._data_bits == 8 else termios.CS7
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[2] &= ~termios.CRTSCTS
        attrs[3] = _parity_const(self._parity)
        attrs[4] = speed
        attrs[5] = speed
        if self._stop_bits == 2:
            attrs[2] |= termios.CSTOPB
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        fcntl.fcntl(fd, fcntl.F_SETFL, os.O_NONBLOCK)
        self._fd = fd

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def write(self, data: bytes) -> int:
        if self._fd is None:
            raise SerialPortError("port not open")
        return os.write(self._fd, data)

    def read(self, max_bytes: int = 4096) -> bytes:
        if self._fd is None:
            raise SerialPortError("port not open")
        try:
            return os.read(self._fd, max_bytes)
        except BlockingIOError:
            return b""


class MemorySerialPort(SerialPortBase):
    """测试/无硬件时用：双向内存管道。"""

    def __init__(self, name: str = "memory://compac", *, peer: Optional["MemorySerialPort"] = None) -> None:
        self._name = name
        self._open = False
        self.rx_buffer = bytearray()
        self.tx_buffer = bytearray()
        self._peer = peer

    @property
    def name(self) -> str:
        return self._name

    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def write(self, data: bytes) -> int:
        if not self._open:
            raise SerialPortError("port not open")
        self.tx_buffer.extend(data)
        if self._peer is not None and self._peer._open:
            self._peer.rx_buffer.extend(data)
        return len(data)

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self._open:
            raise SerialPortError("port not open")
        n = min(max_bytes, len(self.rx_buffer))
        out = bytes(self.rx_buffer[:n])
        del self.rx_buffer[:n]
        return out

    def inject_rx(self, data: bytes) -> None:
        self.rx_buffer.extend(data)


def link_memory_ports(a: MemorySerialPort, b: MemorySerialPort) -> None:
    """双向互联两个内存串口（a.tx → b.rx，b.tx → a.rx）。"""
    a._peer = b
    b._peer = a


def _pyserial_parity(parity: str):
    import serial

    p = (parity or "N").upper()
    if p in ("N", "NONE"):
        return serial.PARITY_NONE
    if p in ("E", "EVEN"):
        return serial.PARITY_EVEN
    if p in ("O", "ODD"):
        return serial.PARITY_ODD
    raise SerialPortError(f"unsupported parity: {parity!r}")


def _pyserial_bytesize(data_bits: int):
    import serial

    if int(data_bits) == 7:
        return serial.SEVENBITS
    if int(data_bits) == 8:
        return serial.EIGHTBITS
    raise SerialPortError(f"unsupported data bits: {data_bits}")


def _pyserial_stopbits(stop_bits: int):
    import serial

    if int(stop_bits) == 1:
        return serial.STOPBITS_ONE
    if int(stop_bits) == 2:
        return serial.STOPBITS_TWO
    raise SerialPortError(f"unsupported stop bits: {stop_bits}")


class PySerialPort(SerialPortBase):
    """Windows/macOS 串口（pyserial，非阻塞读）。"""

    def __init__(
        self,
        device: str,
        *,
        baud_rate: int = 9600,
        data_bits: int = 8,
        parity: str = "N",
        stop_bits: int = 1,
    ) -> None:
        self._device = device
        self._baud = int(baud_rate)
        self._data_bits = int(data_bits)
        self._parity = parity
        self._stop_bits = int(stop_bits)
        self._ser = None

    @property
    def name(self) -> str:
        return self._device

    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self) -> None:
        if self.is_open():
            return
        import serial

        try:
            self._ser = serial.Serial(
                port=self._device,
                baudrate=self._baud,
                bytesize=_pyserial_bytesize(self._data_bits),
                parity=_pyserial_parity(self._parity),
                stopbits=_pyserial_stopbits(self._stop_bits),
                timeout=0,
                write_timeout=5,
            )
        except serial.SerialException as e:
            raise SerialPortError(f"cannot open {self._device}: {e}") from e

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def write(self, data: bytes) -> int:
        if not self.is_open():
            raise SerialPortError("port not open")
        return self._ser.write(data)

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self.is_open():
            raise SerialPortError("port not open")
        waiting = self._ser.in_waiting
        if waiting <= 0:
            return b""
        return self._ser.read(min(max_bytes, waiting))


def create_serial_port(
    device: str,
    *,
    baud_rate: int = 9600,
    data_bits: int = 8,
    parity: str = "N",
    stop_bits: int = 1,
    force_memory: bool = False,
) -> SerialPortBase:
    if force_memory or device.startswith("memory://"):
        return MemorySerialPort(device)
    if sys.platform.startswith("linux") and os.path.exists(device):
        return TermiosSerialPort(
            device,
            baud_rate=baud_rate,
            data_bits=data_bits,
            parity=parity,
            stop_bits=stop_bits,
        )
    if sys.platform.startswith("linux"):
        return TermiosSerialPort(
            device,
            baud_rate=baud_rate,
            data_bits=data_bits,
            parity=parity,
            stop_bits=stop_bits,
        )
    return PySerialPort(
        device,
        baud_rate=baud_rate,
        data_bits=data_bits,
        parity=parity,
        stop_bits=stop_bits,
    )
