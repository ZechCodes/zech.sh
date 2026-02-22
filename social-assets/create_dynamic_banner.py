#!/usr/bin/env python3
"""
Create a dynamic, fun, perfectly looping Discord banner.
Multiple angles burst in, spin wildly, and create an energetic show!
"""

from PIL import Image, ImageDraw
import math
import numpy as np

def ease_in_out_cubic(t):
    """Cubic ease-in-out for smooth animation"""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2

def create_dynamic_banner():
    # Load the angle element
    angle_img = Image.open('angle.png').convert('RGBA')
    print(f"Loaded angle.png: {angle_img.size}")
    
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Animation parameters for perfect loop
    total_frames = 120  # 4 seconds at 30fps
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Create different sized versions
    angle_sizes = [80, 60, 45, 30]
    angle_variants = []
    
    for size in angle_sizes:
        aspect_ratio = angle_img.height / angle_img.width
        new_width = size
        new_height = int(size * aspect_ratio)
        resized = angle_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        angle_variants.append(resized)
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGBA', (banner_width, banner_height), bg_color + (255,))
        
        # Animation progress (0 to 1)
        progress = frame_num / total_frames
        
        # Create multiple exciting effects happening simultaneously
        
        # Effect 1: Large spinning angles crossing the screen
        for i in range(3):
            angle_variant = angle_variants[0]  # Largest size
            
            # Each angle starts at different times for staggered effect
            phase_offset = i * 0.33
            adjusted_progress = (progress + phase_offset) % 1.0
            
            # Dramatic entrance and exit with easing
            if adjusted_progress < 0.8:
                ease_progress = ease_in_out_cubic(adjusted_progress / 0.8)
                x = -angle_variant.width + ease_progress * (banner_width + angle_variant.width * 2)
            else:
                # Quick exit
                exit_progress = (adjusted_progress - 0.8) / 0.2
                x = banner_width + exit_progress * angle_variant.width
            
            # Bouncing Y motion
            y_center = banner_height // 2 + (i - 1) * 60
            y_bounce = math.sin(adjusted_progress * math.pi * 6) * 30
            y = y_center + y_bounce - angle_variant.height // 2
            
            # Wild spinning
            rotation = adjusted_progress * 720 + i * 120  # 2 full rotations
            
            # Create rotated version
            rotated = angle_variant.rotate(rotation, expand=False)
            
            # Dynamic opacity for dramatic effect
            opacity = 1.0
            if adjusted_progress < 0.1:
                opacity = adjusted_progress / 0.1
            elif adjusted_progress > 0.9:
                opacity = (1.0 - adjusted_progress) / 0.1
            
            # Apply opacity
            if opacity > 0 and x > -rotated.width and x < banner_width:
                angle_array = np.array(rotated)
                if angle_array.shape[2] == 4:
                    angle_array[:, :, 3] = (angle_array[:, :, 3] * opacity).astype(np.uint8)
                rotated_with_alpha = Image.fromarray(angle_array)
                canvas.paste(rotated_with_alpha, (int(x), int(y)), rotated_with_alpha)
        
        # Effect 2: Smaller angles in orbital motion
        for i in range(4):
            angle_variant = angle_variants[2]  # Medium-small size
            
            # Orbital centers
            center_x = 150 + i * 100
            center_y = banner_height // 2
            
            # Orbital motion
            orbit_angle = progress * math.pi * 4 + i * math.pi / 2  # 2 full orbits
            orbit_radius = 40 + math.sin(progress * math.pi * 2) * 15  # Pulsing radius
            
            x = center_x + math.cos(orbit_angle) * orbit_radius - angle_variant.width // 2
            y = center_y + math.sin(orbit_angle) * orbit_radius * 0.5 - angle_variant.height // 2  # Elliptical
            
            # Rotation synchronized with orbit
            rotation = -orbit_angle * 180 / math.pi * 2  # Counter-rotating
            
            rotated = angle_variant.rotate(rotation, expand=False)
            
            # Pulsing opacity
            opacity = 0.7 + 0.3 * math.sin(progress * math.pi * 8 + i)
            
            if x > -rotated.width and x < banner_width and y > -rotated.height and y < banner_height:
                angle_array = np.array(rotated)
                if angle_array.shape[2] == 4:
                    angle_array[:, :, 3] = (angle_array[:, :, 3] * opacity).astype(np.uint8)
                rotated_with_alpha = Image.fromarray(angle_array)
                canvas.paste(rotated_with_alpha, (int(x), int(y)), rotated_with_alpha)
        
        # Effect 3: Tiny angles creating a "meteor shower"
        for i in range(8):
            angle_variant = angle_variants[3]  # Smallest size
            
            # Staggered timing
            meteor_progress = (progress + i * 0.125) % 1.0
            
            # Diagonal movement from top-right to bottom-left
            start_x = banner_width + 50
            start_y = -50
            end_x = -50
            end_y = banner_height + 50
            
            x = start_x + (end_x - start_x) * meteor_progress
            y = start_y + (end_y - start_y) * meteor_progress
            
            # Fast rotation
            rotation = meteor_progress * 1080  # 3 full rotations
            
            rotated = angle_variant.rotate(rotation, expand=False)
            
            # Trail effect with opacity
            opacity = 0.6 if 0.1 < meteor_progress < 0.9 else 0.3
            
            if x > -rotated.width and x < banner_width and y > -rotated.height and y < banner_height:
                angle_array = np.array(rotated)
                if angle_array.shape[2] == 4:
                    angle_array[:, :, 3] = (angle_array[:, :, 3] * opacity).astype(np.uint8)
                rotated_with_alpha = Image.fromarray(angle_array)
                canvas.paste(rotated_with_alpha, (int(x), int(y)), rotated_with_alpha)
        
        # Add energy pulses (subtle background effect)
        pulse_overlay = Image.new('RGBA', (banner_width, banner_height), (0, 0, 0, 0))
        pulse_draw = ImageDraw.Draw(pulse_overlay)
        
        # Radial pulse from center
        pulse_intensity = abs(math.sin(progress * math.pi * 6)) * 30
        center_x, center_y = banner_width // 2, banner_height // 2
        pulse_radius = int(pulse_intensity * 3)
        
        if pulse_radius > 0:
            pulse_draw.ellipse([
                (center_x - pulse_radius, center_y - pulse_radius),
                (center_x + pulse_radius, center_y + pulse_radius)
            ], fill=(255, 255, 255, int(pulse_intensity // 3)))
        
        canvas = Image.alpha_composite(canvas, pulse_overlay)
        
        # Convert to RGB for GIF
        final_frame = Image.new('RGB', (banner_width, banner_height), bg_color)
        final_frame.paste(canvas, (0, 0), canvas)
        
        frames.append(final_frame)
        
        if frame_num % 30 == 0:
            print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_dynamic_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_dynamic_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Perfect loop duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_dynamic_banner()