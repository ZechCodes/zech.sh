#!/usr/bin/env python3
"""
Create an animated Discord banner (600x240) using the angle.png element.
Animation concept: Multiple angle elements flowing across the banner in waves,
with varying sizes, opacities, and speeds for a dynamic effect.
"""

from PIL import Image, ImageDraw
import math
import numpy as np

def ease_in_out_sine(t):
    """Sine ease-in-out function for smooth animation"""
    return -(math.cos(math.pi * t) - 1) / 2

def create_discord_banner():
    # Load the angle element
    try:
        angle_img = Image.open('angle.png').convert('RGBA')
        print(f"Angle image dimensions: {angle_img.size}")
        
        # Check if image is too small or empty
        if angle_img.size[0] <= 1 or angle_img.size[1] <= 1:
            print("Angle image is too small, creating a simple chevron shape")
            # Create a simple chevron/angle shape
            angle_img = create_simple_angle()
        else:
            # Check if image has any non-transparent pixels
            angle_array = np.array(angle_img)
            non_transparent = np.sum(angle_array[:, :, 3] > 0)
            print(f"Non-transparent pixels: {non_transparent}")
            if non_transparent == 0:
                print("Angle image is transparent, creating a simple chevron shape")
                angle_img = create_simple_angle()
    except:
        print("Could not load angle.png, creating a simple chevron shape")
        angle_img = create_simple_angle()
    
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240

def create_simple_angle():
    """Create a simple angle/chevron shape"""
    size = 60
    angle_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(angle_img)
    
    # Create a chevron/angle shape
    points = [
        (10, 15),  # Top left
        (30, 15),  # Top middle
        (50, 30),  # Right point
        (30, 45),  # Bottom middle
        (10, 45),  # Bottom left
        (25, 30)   # Left point (back to create angle)
    ]
    
    # Draw the angle shape in white
    draw.polygon(points, fill=(255, 255, 255, 255))
    
    return angle_img
    
    # Animation parameters
    total_frames = 120  # 4 seconds at 30fps for smooth loop
    fps = 30
    bg_color = (0x16, 0x16, 0x16, 255)  # #161616 to match the spinning gif
    
    # Create different sized versions of the angle
    angle_sizes = [60, 40, 30, 20]  # Different sizes for depth
    angle_variants = []
    
    for size in angle_sizes:
        # Resize angle maintaining aspect ratio
        aspect_ratio = angle_img.height / angle_img.width
        new_width = size
        new_height = int(size * aspect_ratio)
        resized_angle = angle_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        angle_variants.append(resized_angle)
    
    # Animation elements - multiple waves of angles
    waves = [
        {
            'angle_idx': 0,  # Largest angles
            'y_pos': 60,
            'speed': 1.5,
            'opacity_base': 0.8,
            'count': 3,
            'spacing': 200,
            'y_offset_amplitude': 20
        },
        {
            'angle_idx': 1,  # Medium angles
            'y_pos': 120,
            'speed': -1.0,  # Opposite direction
            'opacity_base': 0.6,
            'count': 4,
            'spacing': 150,
            'y_offset_amplitude': 15
        },
        {
            'angle_idx': 2,  # Small angles
            'y_pos': 180,
            'speed': 2.0,
            'opacity_base': 0.4,
            'count': 5,
            'spacing': 120,
            'y_offset_amplitude': 10
        },
        {
            'angle_idx': 3,  # Tiny angles
            'y_pos': 30,
            'speed': -0.7,
            'opacity_base': 0.3,
            'count': 6,
            'spacing': 100,
            'y_offset_amplitude': 8
        }
    ]
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGBA', (banner_width, banner_height), bg_color)
        
        # Animation progress (0 to 1)
        progress = frame_num / total_frames
        
        # Draw each wave
        for wave in waves:
            angle_variant = angle_variants[wave['angle_idx']]
            
            # Calculate wave offset
            wave_offset = progress * wave['speed'] * banner_width
            
            # Draw multiple angles in this wave
            for i in range(wave['count']):
                # Calculate position
                base_x = (i * wave['spacing'] + wave_offset) % (banner_width + wave['spacing']) - wave['spacing']
                
                # Add sine wave vertical offset
                sine_offset = math.sin((progress * 2 * math.pi) + (i * 0.5)) * wave['y_offset_amplitude']
                y_pos = wave['y_pos'] + sine_offset
                
                # Calculate opacity with some variation
                opacity_variation = 0.3 * math.sin((progress * 4 * math.pi) + (i * 0.8))
                opacity = wave['opacity_base'] + opacity_variation
                opacity = max(0.1, min(1.0, opacity))  # Clamp between 0.1 and 1.0
                
                # Apply opacity to the angle
                angle_with_alpha = angle_variant.copy()
                angle_array = np.array(angle_with_alpha)
                if angle_array.shape[2] == 4:  # Has alpha channel
                    angle_array[:, :, 3] = (angle_array[:, :, 3] * opacity).astype(np.uint8)
                angle_with_alpha = Image.fromarray(angle_array)
                
                # Paste the angle
                paste_x = int(base_x)
                paste_y = int(y_pos - angle_variant.height // 2)
                
                # Only paste if it's visible
                if paste_x > -angle_variant.width and paste_x < banner_width:
                    canvas.paste(angle_with_alpha, (paste_x, paste_y), angle_with_alpha)
        
        # Add a subtle gradient overlay for depth
        gradient_overlay = Image.new('RGBA', (banner_width, banner_height), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient_overlay)
        
        # Create subtle vertical gradient
        for y in range(banner_height):
            alpha = int(30 * (y / banner_height))  # Subtle darkening towards bottom
            gradient_draw.rectangle([(0, y), (banner_width, y+1)], fill=(0, 0, 0, alpha))
        
        canvas = Image.alpha_composite(canvas, gradient_overlay)
        
        # Convert to RGB for GIF
        frame_rgb = Image.new('RGB', (banner_width, banner_height), bg_color[:3])
        frame_rgb.paste(canvas, (0, 0), canvas)
        
        frames.append(frame_rgb)
        
        print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)  # Duration per frame in milliseconds
    frames[0].save(
        'discord_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,  # Infinite loop
        optimize=True
    )
    
    print(f"Generated discord_banner.gif with {total_frames} frames at {fps}fps")
    print(f"Banner dimensions: {banner_width}x{banner_height}")
    print(f"Animation duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_discord_banner()