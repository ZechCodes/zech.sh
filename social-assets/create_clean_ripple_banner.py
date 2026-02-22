#!/usr/bin/env python3
"""
Create a Discord banner with a grid of blocks where random cells
light up at max brightness and create ripples that interfere.
"""

from PIL import Image, ImageDraw
import math
import random

def create_clean_ripple_banner():
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Grid parameters
    block_size = 12
    gap = 2
    grid_width = banner_width // (block_size + gap)
    grid_height = banner_height // (block_size + gap)
    
    # Animation parameters for perfect loop
    total_frames = 120  # 4 seconds at 30fps
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Pre-generate ripple events for perfect looping
    # Each event has: frame, grid_x, grid_y
    random.seed(42)  # Fixed seed for reproducible pattern
    ripple_events = []
    
    # Generate ripple events spread throughout the animation
    for _ in range(20):  # 20 random ripples over the loop
        frame = random.randint(0, total_frames - 1)
        grid_x = random.randint(0, grid_width - 1)
        grid_y = random.randint(0, grid_height - 1)
        ripple_events.append({'frame': frame, 'x': grid_x, 'y': grid_y})
    
    # Sort by frame for easier processing
    ripple_events.sort(key=lambda x: x['frame'])
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Calculate intensity for each grid cell
        for grid_x in range(grid_width):
            for grid_y in range(grid_height):
                intensity = 0.05  # Base dim level
                
                # Check all ripple events to see their effect on this cell
                for event in ripple_events:
                    # Calculate how many frames since this ripple started
                    frames_since = frame_num - event['frame']
                    
                    # Handle looping - ripples from "future" events in previous loop
                    if frames_since < 0:
                        frames_since += total_frames
                    
                    # Only process if ripple is still active (within 60 frames)
                    if 0 <= frames_since < 60:
                        # Distance from ripple source
                        dx = grid_x - event['x']
                        dy = grid_y - event['y']
                        distance = math.sqrt(dx*dx + dy*dy)
                        
                        # Ripple speed and timing
                        ripple_radius = frames_since * 0.5  # How far the ripple has traveled
                        
                        # Check if ripple wave has reached this cell
                        # Wave has thickness of about 2 units
                        if abs(distance - ripple_radius) < 2:
                            # Calculate wave intensity based on distance from wave center
                            wave_center_distance = abs(distance - ripple_radius)
                            wave_intensity = (2 - wave_center_distance) / 2  # 1.0 at center, 0.0 at edge
                            
                            # Fade over time
                            time_fade = max(0, 1 - frames_since / 60)
                            
                            # Distance fade (farther from source = dimmer)
                            distance_fade = 1.0 / (1.0 + distance * 0.1)
                            
                            # Add to intensity
                            intensity += wave_intensity * time_fade * distance_fade
                
                # Special case: if this cell is the source of a ripple this frame
                for event in ripple_events:
                    if event['frame'] == frame_num and event['x'] == grid_x and event['y'] == grid_y:
                        intensity = 1.0  # Max brightness at source
                
                # Clamp intensity
                intensity = max(0, min(1, intensity))
                
                # Convert to grayscale color
                gray_value = int(intensity * 255)
                color = (gray_value, gray_value, gray_value)
                
                # Calculate pixel position
                pixel_x = grid_x * (block_size + gap)
                pixel_y = grid_y * (block_size + gap)
                
                # Draw the block
                if intensity > 0.05:  # Only draw if visible enough
                    draw.rectangle([
                        (pixel_x, pixel_y),
                        (pixel_x + block_size, pixel_y + block_size)
                    ], fill=color)
        
        frames.append(canvas)
        
        if frame_num % 30 == 0:
            print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_clean_ripple_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_clean_ripple_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Grid: {grid_width}x{grid_height} blocks")
    print(f"Perfect loop duration: {total_frames / fps:.1f} seconds")
    print(f"Ripple events: {len(ripple_events)}")

if __name__ == '__main__':
    create_clean_ripple_banner()