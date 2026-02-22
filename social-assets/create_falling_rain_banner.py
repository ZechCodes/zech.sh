#!/usr/bin/env python3
"""
Create a Discord banner with random cells lighting up and falling down
the grid, leaving fading trails like digital rain.
"""

from PIL import Image, ImageDraw
import random

class FallingDrop:
    def __init__(self, x, grid_height):
        self.x = x
        self.y = 0  # Start at the very top of the grid
        self.trail = []  # Store positions for trail
        self.max_trail_length = random.randint(8, 15)  # Variable trail length
        self.speed = random.choice([1, 1, 1, 2])  # Mostly speed 1, sometimes 2
        self.brightness = random.uniform(0.6, 1.0)  # Variable brightness
        self.active = True
        self.frames_since_move = 0
        self.grid_height = grid_height
    
    def update(self):
        if not self.active:
            return
        
        self.frames_since_move += 1
        
        # Move based on speed (every frame or every other frame)
        if self.frames_since_move >= (3 - self.speed):  # speed 1 = every 2 frames, speed 2 = every frame
            self.frames_since_move = 0
            
            # Add current position to trail
            self.trail.append(self.y)
            
            # Move down
            self.y += 1
            
            # Keep trail at max length
            if len(self.trail) > self.max_trail_length:
                self.trail.pop(0)
            
            # Deactivate if completely off screen
            if self.y > self.grid_height + self.max_trail_length:
                self.active = False
    
    def draw(self, draw, block_size, gap):
        if not self.active or len(self.trail) == 0:
            return
        
        # Draw trail
        for i, trail_y in enumerate(self.trail):
            if 0 <= trail_y < self.grid_height:
                # Calculate fade based on position in trail
                age_factor = (len(self.trail) - i) / len(self.trail)
                intensity = self.brightness * age_factor
                
                # Head of the trail is brightest
                if i == len(self.trail) - 1:
                    intensity = 1.0
                
                gray_value = int(intensity * 255)
                color = (gray_value, gray_value, gray_value)
                
                pixel_x = self.x * (block_size + gap)
                pixel_y = trail_y * (block_size + gap)
                
                draw.rectangle([
                    (pixel_x, pixel_y),
                    (pixel_x + block_size, pixel_y + block_size)
                ], fill=color)

def create_falling_rain_banner():
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Grid parameters
    block_size = 8
    gap = 2  # Increase gap for better separation
    grid_width = banner_width // (block_size + gap)
    grid_height = banner_height // (block_size + gap)
    
    print(f"Grid size: {grid_width}x{grid_height}")
    
    # Animation parameters for perfect loop
    total_frames = 120  # 4 seconds at 30fps for smoother loop
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Fixed seed for reproducible animation
    random.seed(42)
    
    # Falling drops
    drops = []
    
    # Pre-fill the grid to look like mid-rainfall
    print("Pre-filling grid for mid-rainfall effect...")
    for x in range(grid_width):
        if random.random() < 0.3:  # 30% chance per column to have a drop
            # Create drops at various positions down the column
            start_y = random.randint(0, grid_height - 5)
            drop = FallingDrop(x, grid_height)
            drop.y = start_y
            
            # Pre-fill the trail
            trail_length = random.randint(3, 8)
            for i in range(trail_length):
                if start_y - i >= 0:
                    drop.trail.append(start_y - i)
            drop.trail.reverse()  # Correct order
            drops.append(drop)
    
    # Spawn rate - how often new drops appear
    spawn_rate = 0.12  # 12% chance per column per frame
    
    frames = []
    
    for frame_num in range(total_frames):
        # Spawn new drops randomly only at the top
        for x in range(grid_width):
            if random.random() < spawn_rate:
                # Don't spawn if there's already a drop at the top of this column
                top_drop = any(drop.x == x and drop.y <= 2 and drop.active for drop in drops)
                if not top_drop:
                    drops.append(FallingDrop(x, grid_height))
        
        # Update all drops
        for drop in drops:
            drop.update()
        
        # Remove inactive drops
        drops = [drop for drop in drops if drop.active]
        
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Draw all drops
        for drop in drops:
            drop.draw(draw, block_size, gap)
        
        # Add some occasional bright flashes at random positions (reduced frequency)
        if random.random() < 0.01:  # 1% chance per frame
            flash_x = random.randint(0, grid_width - 1)
            flash_y = random.randint(0, grid_height - 1)
            pixel_x = flash_x * (block_size + gap)
            pixel_y = flash_y * (block_size + gap)
            
            # Bright white flash
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
        'discord_falling_rain_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_falling_rain_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Grid: {grid_width}x{grid_height} blocks")
    print(f"Animation duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_falling_rain_banner()