#!/usr/bin/env python3
"""
Create a Discord banner with Conway's Game of Life where previous
generations fade out, creating trails. Uses oscillators for looping.
"""

from PIL import Image, ImageDraw
import numpy as np

def create_gameoflife_banner():
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Grid parameters
    block_size = 8
    gap = 1
    grid_width = banner_width // (block_size + gap)
    grid_height = banner_height // (block_size + gap)
    
    print(f"Grid size: {grid_width}x{grid_height}")
    
    # Animation parameters
    total_frames = 180  # 6 seconds at 30fps
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Game of Life state
    # Just keep current state, no history needed
    current_grid = None
    
    # Initialize with random patterns and chaos
    def initialize_grid():
        grid = np.zeros((grid_height, grid_width), dtype=bool)
        
        # Start with random noise
        import random
        random.seed()  # Use random seed each time
        
        # Fill with random cells (about 15% density)
        for y in range(grid_height):
            for x in range(grid_width):
                if random.random() < 0.15:
                    grid[y, x] = True
        
        # Add some random interesting patterns
        patterns = []
        
        # Random blinkers
        for _ in range(random.randint(3, 8)):
            x = random.randint(1, grid_width-3)
            y = random.randint(1, grid_height-3)
            if random.choice([True, False]):
                # Vertical blinker
                grid[y, x] = True
                grid[y+1, x] = True
                grid[y+2, x] = True
            else:
                # Horizontal blinker
                grid[y, x] = True
                grid[y, x+1] = True
                grid[y, x+2] = True
        
        # Random gliders
        for _ in range(random.randint(2, 5)):
            x = random.randint(2, grid_width-4)
            y = random.randint(2, grid_height-4)
            # Random glider orientation
            if random.choice([True, False, True, False]):  # Favor normal glider
                grid[y, x+1] = True
                grid[y+1, x+2] = True
                grid[y+2, x] = True
                grid[y+2, x+1] = True
                grid[y+2, x+2] = True
            else:
                # Flipped glider
                grid[y, x] = True
                grid[y, x+1] = True
                grid[y, x+2] = True
                grid[y+1, x] = True
                grid[y+2, x+1] = True
        
        # Random blocks (stable patterns)
        for _ in range(random.randint(2, 6)):
            x = random.randint(1, grid_width-3)
            y = random.randint(1, grid_height-3)
            grid[y, x] = True
            grid[y, x+1] = True
            grid[y+1, x] = True
            grid[y+1, x+1] = True
        
        # Random beehives (stable patterns)
        for _ in range(random.randint(1, 4)):
            x = random.randint(2, grid_width-4)
            y = random.randint(1, grid_height-3)
            grid[y, x+1] = True
            grid[y, x+2] = True
            grid[y+1, x] = True
            grid[y+1, x+3] = True
            grid[y+2, x+1] = True
            grid[y+2, x+2] = True
        
        # Add some random density clusters for chaos
        for _ in range(random.randint(2, 5)):
            cluster_x = random.randint(3, grid_width-4)
            cluster_y = random.randint(3, grid_height-4)
            cluster_size = random.randint(3, 6)
            
            for _ in range(cluster_size * 2):
                dx = random.randint(-cluster_size//2, cluster_size//2)
                dy = random.randint(-cluster_size//2, cluster_size//2)
                nx = cluster_x + dx
                ny = cluster_y + dy
                if 0 <= nx < grid_width and 0 <= ny < grid_height:
                    if random.random() < 0.6:
                        grid[ny, nx] = True
        
        return grid
    
    def game_of_life_step(grid):
        """Apply one step of Conway's Game of Life"""
        new_grid = np.zeros_like(grid)
        
        for y in range(grid_height):
            for x in range(grid_width):
                # Count neighbors (with wrapping for interesting edge behavior)
                neighbors = 0
                for dy in [-1, 0, 1]:
                    for dx in [-1, 0, 1]:
                        if dx == 0 and dy == 0:
                            continue
                        ny = (y + dy) % grid_height
                        nx = (x + dx) % grid_width
                        if grid[ny, nx]:
                            neighbors += 1
                
                # Apply Game of Life rules
                if grid[y, x]:  # Cell is alive
                    if neighbors == 2 or neighbors == 3:
                        new_grid[y, x] = True  # Survives
                else:  # Cell is dead
                    if neighbors == 3:
                        new_grid[y, x] = True  # Birth
        
        return new_grid
    
    # Initialize the grid
    current_grid = initialize_grid()
    
    frames = []
    
    for frame_num in range(total_frames):
        # Every 3 frames, advance the Game of Life
        if frame_num % 3 == 0:
            current_grid = game_of_life_step(current_grid)
            
            # Add some random mutations to keep things interesting
            import random
            if frame_num > 30:  # After initial evolution
                # Small chance to add random cells
                if random.random() < 0.1:  # 10% chance per step
                    for _ in range(random.randint(1, 3)):
                        x = random.randint(0, grid_width-1)
                        y = random.randint(0, grid_height-1)
                        current_grid[y, x] = True
                
                # Small chance to kill random cells
                if random.random() < 0.05:  # 5% chance per step
                    for _ in range(random.randint(1, 2)):
                        x = random.randint(0, grid_width-1)
                        y = random.randint(0, grid_height-1)
                        current_grid[y, x] = False
                
                # Occasionally add a new glider for chaos
                if random.random() < 0.03:  # 3% chance per step
                    x = random.randint(2, grid_width-4)
                    y = random.randint(2, grid_height-4)
                    if random.choice([True, False]):
                        current_grid[y, x+1] = True
                        current_grid[y+1, x+2] = True
                        current_grid[y+2, x] = True
                        current_grid[y+2, x+1] = True
                        current_grid[y+2, x+2] = True
        
        # Create canvas
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Draw only the current generation
        for y in range(grid_height):
            for x in range(grid_width):
                if current_grid[y, x]:
                    # White blocks for living cells
                    color = (255, 255, 255)
                    
                    # Calculate pixel position
                    pixel_x = x * (block_size + gap)
                    pixel_y = y * (block_size + gap)
                    
                    # Draw the block
                    draw.rectangle([
                        (pixel_x, pixel_y),
                        (pixel_x + block_size, pixel_y + block_size)
                    ], fill=color)
        
        frames.append(canvas)
        
        if frame_num % 30 == 0:
            print(f"Generated frame {frame_num + 1}/{total_frames}")
    
    # Simple restart for looping - just fade out and restart
    print("Creating loop transition...")
    
    transition_frames = 15
    for i in range(transition_frames):
        fade_factor = 1 - (i / transition_frames)  # Fade out
        
        canvas = Image.new('RGB', (banner_width, banner_height), bg_color)
        draw = ImageDraw.Draw(canvas)
        
        # Draw current grid with fading
        for y in range(grid_height):
            for x in range(grid_width):
                if current_grid[y, x]:
                    intensity = int(255 * fade_factor)
                    color = (intensity, intensity, intensity)
                    
                    pixel_x = x * (block_size + gap)
                    pixel_y = y * (block_size + gap)
                    
                    draw.rectangle([
                        (pixel_x, pixel_y),
                        (pixel_x + block_size, pixel_y + block_size)
                    ], fill=color)
        
        frames.append(canvas)
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_gameoflife_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    total_duration = len(frames) / fps
    print(f"Generated discord_gameoflife_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Animation duration: {total_duration:.1f} seconds")
    print(f"Total frames: {len(frames)}")

if __name__ == '__main__':
    create_gameoflife_banner()