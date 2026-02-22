#!/usr/bin/env python3
"""
Create a Discord banner with perfect looping digital rain.
Pre-calculates all drop movements to ensure perfect loop.
"""

from PIL import Image, ImageDraw
import random

def create_perfect_loop_rain():
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Grid parameters
    block_size = 8
    gap = 2
    grid_width = banner_width // (block_size + gap)
    grid_height = banner_height // (block_size + gap)
    
    print(f"Grid size: {grid_width}x{grid_height}")
    
    # Animation parameters
    total_frames = 120  # 4 seconds at 30fps
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Pre-calculate all drop movements for perfect loop
    print("Pre-calculating drop movements for perfect loop...")
    
    # Define drops that will loop perfectly
    drop_patterns = []
    
    # Create deterministic drop patterns
    random.seed(42)
    for x in range(grid_width):
        # Maximum drops per column for torrential rain
        num_drops = random.choice([2, 3, 3, 4])  # 25% get 2 drops, 50% get 3 drops, 25% get 4 drops
        
        for drop_idx in range(num_drops):
            # Each drop has a specific start frame and properties
            start_frame = random.randint(0, total_frames - 1)
            trail_length = random.randint(4, 8)  # Shorter trails for maximum density
            speed = random.choice([1, 1, 2, 2, 3, 3, 4])  # Much more speed variety: 1=slow, 2=medium, 3=fast, 4=very fast
            brightness = random.uniform(0.5, 1.0)
            
            drop_patterns.append({
                'x': x,
                'start_frame': start_frame,
                'trail_length': trail_length,
                'speed': speed,
                'brightness': brightness
            })
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Draw all active drops for this frame
        for pattern in drop_patterns:
            # Calculate where this drop should be at this frame
            frames_since_start = (frame_num - pattern['start_frame']) % total_frames
            
            # Calculate movement (accounting for speed)
            if pattern['speed'] == 1:
                # Slow: Move every 3 frames
                movement_frames = frames_since_start // 3
            elif pattern['speed'] == 2:
                # Medium: Move every 2 frames
                movement_frames = frames_since_start // 2
            elif pattern['speed'] == 3:
                # Fast: Move every frame
                movement_frames = frames_since_start
            else:  # speed == 4
                # Very fast: Move 2 cells every frame
                movement_frames = frames_since_start * 2
            
            # Current position (starts at top)
            current_y = movement_frames
            
            # Only draw if drop is on screen
            total_drop_height = pattern['trail_length'] + 5  # Add buffer
            if current_y >= -pattern['trail_length'] and current_y <= grid_height + pattern['trail_length']:
                
                # Draw trail
                for trail_pos in range(pattern['trail_length']):
                    trail_y = current_y - trail_pos
                    
                    # Only draw if on grid
                    if 0 <= trail_y < grid_height:
                        # Calculate fade (head is brightest)
                        fade_factor = (pattern['trail_length'] - trail_pos) / pattern['trail_length']
                        intensity = pattern['brightness'] * fade_factor
                        
                        # Head of trail is always max brightness
                        if trail_pos == 0:
                            intensity = 1.0
                        
                        gray_value = int(intensity * 255)
                        color = (gray_value, gray_value, gray_value)
                        
                        pixel_x = pattern['x'] * (block_size + gap)
                        pixel_y = trail_y * (block_size + gap)
                        
                        draw.rectangle([
                            (pixel_x, pixel_y),
                            (pixel_x + block_size, pixel_y + block_size)
                        ], fill=color)
        
        # Add occasional flashes (deterministic based on frame)
        random.seed(frame_num + 1000)  # Different seed per frame but consistent
        if random.random() < 0.015:  # Higher chance for more intensity
            flash_x = random.randint(0, grid_width - 1)
            flash_y = random.randint(0, grid_height - 1)
            pixel_x = flash_x * (block_size + gap)
            pixel_y = flash_y * (block_size + gap)
            
            draw.rectangle([
                (pixel_x, pixel_y),
                (pixel_x + block_size, pixel_y + block_size)
            ], fill=(255, 255, 255))
        
        frames.append(canvas)
        
        if frame_num % 30 == 0:
            print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_perfect_loop_rain.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_perfect_loop_rain.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Grid: {grid_width}x{grid_height} blocks")
    print(f"Perfect loop duration: {total_frames / fps:.1f} seconds")
    print(f"Drop patterns: {len(drop_patterns)}")

if __name__ == '__main__':
    create_perfect_loop_rain()