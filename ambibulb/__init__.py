"""ambibulb -- drive Wipro/Tuya smart bulbs from your screen color, locally.

A small ambient-lighting engine: it samples the screen, extracts color
(whole-screen or per-zone, video-aware), and streams it to one or more Tuya
bulbs over the LAN -- no cloud at runtime.

Public modules:
    config     -- bulb configuration loading/validation
    color      -- color extraction and HSV shaping (pure functions)
    capture    -- screen sampling
    bulb       -- a single bulb's connection + worker thread
    engine     -- the live streaming loop
    discovery  -- LAN scan for Tuya devices
    cli        -- command-line entry point
"""

__version__ = "1.0.0"
