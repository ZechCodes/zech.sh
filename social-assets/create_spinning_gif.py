#!/usr/bin/env python3
"""
Create an animated GIF of trized.png spinning with ghost trails.
- 60 frames total at 20fps
- 15 frames wait at start
- 30 frames of spinning (5 full rotations with acceleration/deceleration)
- 15 frames wait at end
- Ghost trails that fade over 5 frames
- #222222 background color
"""

from PIL import Image, ImageDraw
import math
import numpy as np

def ease_in_out_cubic(t):
    """Cubic ease-in-out function for smooth acceleration/deceleration"""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2

def create_spinning_gif():
    # Load the source image
    source_img = Image.open('trized.png').convert('RGBA')
    
    # Since the trized is now optically centered with its own margins, scale it to fill the canvas
    original_width, original_height = source_img.size
    
    # Scale the square trized to 240x240 to match the GIF canvas
    final_layer_size = 240
    final_layer = source_img.resize((final_layer_size, final_layer_size), Image.Resampling.LANCZOS)
    
    # Animation parameters (30fps: scale up by 1.5x to maintain timing)
    total_frames = 113  # 23 + 67 + 23 (rounded from 112.5)
    wait_frames = 23    # 15 * 1.5 = 22.5, rounded to 23
    spin_frames = 67    # 45 * 1.5 = 67.5, rounded to 67
    fps = 30
    bg_color = (0x16, 0x16, 0x16, 255)  # #161616
    
    # Fixed canvas size
    canvas_size = 240
    center = canvas_size // 2
    
    # Ghost trail parameters
    ghost_fade_frames = 15  # Scaled up for 30fps (10 * 1.5)
    ghost_trails = []  # Will store (image, opacity, frame_created)
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGBA', (canvas_size, canvas_size), bg_color)
        
        # Determine rotation angle
        if frame_num < wait_frames:
            # Initial wait period - no rotation
            angle = 0
        elif frame_num < wait_frames + spin_frames:
            # Spinning period
            spin_progress = (frame_num - wait_frames) / spin_frames
            # Use ease-in-out for smooth acceleration/deceleration
            eased_progress = ease_in_out_cubic(spin_progress)
            # 3 full rotations = 3 * 360 degrees
            angle = eased_progress * 3 * 360
        else:
            # Final wait period - back to no rotation (complete loop)
            angle = 0
        
        # Make a copy of the final layer and rotate it
        # Using expand=False keeps the canvas at the same size, eliminating wobble
        rotated = final_layer.copy().rotate(angle, expand=False, center=(120, 120))
        
        # Position the rotated square at the center of the main canvas
        paste_x = center - rotated.width // 2
        paste_y = center - rotated.height // 2
        
        # Add ghost trails if we're in the spinning phase
        if wait_frames <= frame_num < wait_frames + spin_frames:
            # Add current rotated image to ghost trails
            ghost_trails.append((rotated.copy(), 255, frame_num))
        
        # Remove old ghost trails that have faded completely
        ghost_trails = [(img, opacity, created_frame) for img, opacity, created_frame in ghost_trails 
                       if frame_num - created_frame < ghost_fade_frames]
        
        # Draw ghost trails
        for ghost_img, base_opacity, created_frame in ghost_trails:
            frames_since_created = frame_num - created_frame
            if frames_since_created > 0:  # Don't draw the current frame as a ghost
                # Calculate fade opacity (linear fade over ghost_fade_frames)
                fade_factor = max(0, 1 - frames_since_created / ghost_fade_frames)
                ghost_opacity = fade_factor * 0.8  # Make ghosts more visible
                
                if ghost_opacity > 0:
                    # Create a faded version of the ghost
                    ghost_with_alpha = ghost_img.copy()
                    # Apply opacity to the ghost
                    ghost_array = np.array(ghost_with_alpha)
                    if ghost_array.shape[2] == 4:  # Has alpha channel
                        ghost_array[:, :, 3] = (ghost_array[:, :, 3] * ghost_opacity).astype(np.uint8)
                    ghost_with_alpha = Image.fromarray(ghost_array)
                    
                    canvas.paste(ghost_with_alpha, (paste_x, paste_y), ghost_with_alpha)
        
        # Paste the current rotated image on top
        canvas.paste(rotated, (paste_x, paste_y), rotated)
        
        # Convert to RGB for GIF format
        frame_rgb = Image.new('RGB', (canvas_size, canvas_size), bg_color[:3])
        frame_rgb.paste(canvas, (0, 0), canvas)
        
        frames.append(frame_rgb)
        
        print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)  # Duration per frame in milliseconds
    frames[0].save(
        'trized_spinning.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,  # Infinite loop
        optimize=True
    )
    
    print(f"Generated trized_spinning.gif with {total_frames} frames at {fps}fps")
    print(f"Animation duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_spinning_gif()