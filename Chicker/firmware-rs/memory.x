/* RP2040 with Winbond W25Q128JVSIQ: 16 MB QSPI flash, 264 kB SRAM.
   BOOT2 is the 256-byte second-stage bootloader placed at the very start
   of flash (provided by embassy-rp's boot2-w25q080 feature, which uses the
   same command set / QE-bit handling as the W25Q128JV). */
MEMORY {
    BOOT2 : ORIGIN = 0x10000000, LENGTH = 0x100
    FLASH : ORIGIN = 0x10000100, LENGTH = 16384K - 0x100
    RAM   : ORIGIN = 0x20000000, LENGTH = 256K
}
