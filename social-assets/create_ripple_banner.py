#!/usr/bin/env python3
"""
Create a Discord banner with a grid of pulsing grayscale blocks
that create ripple effects across the banner.
"""

from PIL import Image, ImageDraw
import math
import random

def create_ripple_banner():
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Grid parameters
    block_size = 12
    gap = 2
    grid_width = banner_width // (block_size + gap)
    grid_height = banner_height // (block_size + gap)
    
    # Animation parameters
    total_frames = 150  # 5 seconds at 30fps for smooth ripples
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Ripple sources - multiple points that create ripples
    ripple_sources = [
        {'x': grid_width * 0.2, 'y': grid_height * 0.3, 'phase': 0, 'speed': 0.15, 'amplitude': 1.0},
        {'x': grid_width * 0.8, 'y': grid_height * 0.7, 'phase': math.pi, 'speed': 0.12, 'amplitude': 0.8},
        {'x': grid_width * 0.5, 'y': grid_height * 0.5, 'phase': math.pi/2, 'speed': 0.18, 'amplitude': 0.6},
        {'x': grid_width * 0.1, 'y': grid_height * 0.8, 'phase': math.pi*1.5, 'speed': 0.14, 'amplitude': 0.7},
        {'x': grid_width * 0.9, 'y': grid_height * 0.2, 'phase': math.pi*0.7, 'speed': 0.16, 'amplitude': 0.9}
    ]
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Animation progress
        progress = frame_num / total_frames
        time = progress * 2 * math.pi  # Full cycle
        
        # Calculate intensity for each grid cell
        for grid_x in range(grid_width):
            for grid_y in range(grid_height):
                # Start with base intensity
                intensity = 0.1
                
                # Add ripples from each source
                for source in ripple_sources:
                    # Distance from ripple source
                    dx = grid_x - source['x']
                    dy = grid_y - source['y']
                    distance = math.sqrt(dx*dx + dy*dy)
                    
                    # Ripple wave calculation
                    wave_phase = distance * source['speed'] - time + source['phase']
                    wave_intensity = math.sin(wave_phase) * source['amplitude']
                    
                    # Distance falloff
                    falloff = 1.0 / (1.0 + distance * 0.1)
                    
                    # Add to total intensity
                    intensity += wave_intensity * falloff
                
                # Add some random sparkle
                if random.random() < 0.02:  # 2% chance per frame
                    intensity += random.uniform(0.3, 0.8)
                
                # Clamp intensity between 0 and 1
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
        
        # Add some larger pulse effects
        for i, source in enumerate(ripple_sources):
            # Create expanding circles for dramatic effect
            center_x = source['x'] * (block_size + gap) + block_size // 2
            center_y = source['y'] * (block_size + gap) + block_size // 2
            
            # Pulsing radius
            pulse_progress = (time + source['phase']) % (2 * math.pi)
            pulse_radius = abs(math.sin(pulse_progress)) * 80
            
            if pulse_radius > 5:
                # Outer glow
                glow_intensity = int((1 - pulse_radius/80) * 100)
                if glow_intensity > 10:
                    glow_color = (glow_intensity, glow_intensity, glow_intensity)
                    draw.ellipse([
                        (center_x - pulse_radius, center_y - pulse_radius),
                        (center_x + pulse_radius, center_y + pulse_radius)
                    ], outline=glow_color, width=2)
        
        # Add subtle horizontal scan lines for tech feel
        scan_y = int((progress * 3) % 1 * banner_height)
        for offset in [-2, -1, 0, 1, 2]:
            y = scan_y + offset
            if 0 <= y < banner_height:
                intensity = max(0, 40 - abs(offset) * 10)
                scan_color = (intensity, intensity, intensity)
                draw.line([(0, y), (banner_width, y)], fill=scan_color, width=1)
        
        frames.append(canvas)
        
        if frame_num % 30 == 0:
            print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_ripple_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_ripple_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Grid: {grid_width}x{grid_height} blocks")
    print(f"Animation duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_ripple_banner()