#!/usr/bin/env python3
"""
Create an animated Discord banner (600x240) with flowing chevron elements.
"""

from PIL import Image, ImageDraw
import math

def create_discord_banner():
    # Load the angle element
    angle_img = Image.open('angle.png').convert('RGBA')
    print(f"Loaded angle.png: {angle_img.size}")
    
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Animation parameters
    total_frames = 90  # 3 seconds at 30fps
    fps = 30
    bg_color = (0x16, 0x16, 0x16)  # #161616
    
    # Create different sized versions
    angle_sizes = [50, 35, 25]
    angle_variants = []
    
    for size in angle_sizes:
        resized = angle_img.resize((size, size), Image.Resampling.LANCZOS)
        angle_variants.append(resized)
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        canvas_rgba = Image.new('RGBA', (banner_width, banner_height), bg_color + (255,))
        
        # Animation progress
        progress = frame_num / total_frames
        
        # Draw three waves of chevrons
        waves = [
            {'size_idx': 0, 'y': 60, 'speed': 2.0, 'opacity': 0.8, 'count': 4},
            {'size_idx': 1, 'y': 120, 'speed': -1.5, 'opacity': 0.6, 'count': 5},
            {'size_idx': 2, 'y': 180, 'speed': 2.5, 'opacity': 0.4, 'count': 6}
        ]
        
        for wave in waves:
            chevron = angle_variants[wave['size_idx']]
            
            for i in range(wave['count']):
                # Calculate position
                x_offset = progress * wave['speed'] * banner_width
                x_pos = (i * 120 + x_offset) % (banner_width + 120) - 60
                
                # Add slight vertical movement
                y_offset = math.sin((progress * 4 * math.pi) + (i * 0.5)) * 10
                y_pos = wave['y'] + y_offset - chevron.height // 2
                
                # Only paste if visible
                if x_pos > -chevron.width and x_pos < banner_width:
                    # Create version with opacity
                    temp_img = Image.new('RGBA', (banner_width, banner_height), (0, 0, 0, 0))
                    temp_img.paste(chevron, (int(x_pos), int(y_pos)), chevron)
                    
                    # Apply opacity
                    pixels = temp_img.load()
                    for y in range(banner_height):
                        for x in range(banner_width):
                            r, g, b, a = pixels[x, y]
                            if a > 0:
                                pixels[x, y] = (r, g, b, int(a * wave['opacity']))
                    
                    canvas_rgba = Image.alpha_composite(canvas_rgba, temp_img)
        
        # Convert back to RGB
        final_frame = Image.new('RGB', (banner_width, banner_height), bg_color)
        final_frame.paste(canvas_rgba, (0, 0), canvas_rgba)
        
        frames.append(final_frame)
        print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_banner.gif - {banner_width}x{banner_height} at {fps}fps")

if __name__ == '__main__':
    create_discord_banner()