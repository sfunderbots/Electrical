import uctypes
import struct

DMA_BASE  = 0x50000000
PIO0_BASE = 0x50200000
PIO1_BASE = 0x50300000

def sm_dma_put_ring(chan, sm, src, nword, ring_size):
    """
    DMA transfer to PIO TX FIFO with ring buffer support.

    chan      : DMA channel (0-11)
    sm        : PIO state machine number (0-7, where 4-7 = PIO1 sm0-3)
    src       : array.array source buffer (must be 8-byte aligned for ring_size=3)
    nword     : total number of 32-bit words to transfer
                for ring: set this to n_cycles * 2 (loops over src automatically)
    ring_size : 2^ring_size = number of BYTES to wrap over on read address
                ring_size=3 wraps every 8 bytes = 2 x 32-bit words
                ring_size=0 disables ring (normal DMA)

    The ring buffer means DMA loops over the same 2-word [on_ticks, off_ticks]
    buffer for nword total transfers — only 8 bytes of RAM needed regardless
    of how many cycles or how long the duration is.
    """
    chan_base = DMA_BASE + chan * 0x40

    if sm < 4:
        pio_base = PIO0_BASE
        TREQ_SEL = sm
    else:
        sm       = sm % 4
        pio_base = PIO1_BASE
        TREQ_SEL = sm + 8

    # PIO TX FIFO address for this state machine
    txf_addr = pio_base + 0x10 + sm * 4

    # Get address of source buffer
    # memoryview(src) gives a raw view of the array.array bytes
    # uctypes.addressof() returns the physical address of that memory
    src_addr = uctypes.addressof(memoryview(src))

    # Build DMA control word
    ctrl = ((1 << 21) |           # IRQ_QUIET   — don't fire IRQ on completion
            (TREQ_SEL << 15) |    # TREQ_SEL    — PIO TX FIFO data request
            (0 << 11) |           # CHAIN_TO    — no chaining
            (0 << 10) |           # RING_SEL    — ring on READ address (not write)
            (ring_size << 6) |    # RING_SIZE   — 2^ring_size bytes to wrap over
            (0 << 5) |            # INCR_WRITE  — don't increment write address
            (1 << 4) |            # INCR_READ   — increment read address (wraps via ring)
            (2 << 2) |            # DATA_SIZE   — 32-bit words
            (1 << 1) |            # HIGH_PRIORITY
            (1 << 0))             # EN          — enable and trigger

    # Write all 4 DMA registers at once
    # Writing CTRL_TRIG (offset 12) last triggers the transfer immediately
    mem = uctypes.bytearray_at(chan_base, 16)
    struct.pack_into("<LLLL", mem, 0,
        src_addr,   # READ_ADDR  (offset 0)
        txf_addr,   # WRITE_ADDR (offset 4)
        nword,      # TRANS_COUNT (offset 8)
        ctrl)       # CTRL_TRIG  (offset 12) — triggers on write