#!/usr/bin/env python3
"""
Create an animated Discord banner with physics-based bouncing angle elements.
Inspired by the trized spinning gif with rotation and trails.
"""

from PIL import Image, ImageDraw
import math
import random
import numpy as np

class BouncingAngle:
    def __init__(self, x, y, vx, vy, size, banner_width, banner_height, angle_img):
        self.x = x
        self.y = y
        self.vx = vx  # velocity x
        self.vy = vy  # velocity y
        self.size = size
        self.rotation = random.uniform(0, 360)
        self.rotation_speed = random.uniform(-5, 5)
        self.banner_width = banner_width
        self.banner_height = banner_height
        self.trail = []  # Store previous positions for trail effect
        self.max_trail_length = 8
        self.gravity = 0.15
        self.damping = 0.98
        self.bounce_damping = 0.8
        
        # Create sized version of angle
        aspect_ratio = angle_img.height / angle_img.width
        new_width = size
        new_height = int(size * aspect_ratio)
        self.angle_img = angle_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        self.width = new_width
        self.height = new_height
    
    def update(self):
        # Store current position in trail
        self.trail.append((self.x, self.y, self.rotation))
        if len(self.trail) > self.max_trail_length:
            self.trail.pop(0)
        
        # Apply gravity
        self.vy += self.gravity
        
        # Apply damping (air resistance)
        self.vx *= self.damping
        self.vy *= self.damping
        
        # Update position
        self.x += self.vx
        self.y += self.vy
        
        # Update rotation
        self.rotation += self.rotation_speed
        
        # Bounce off walls
        if self.x <= 0 or self.x >= self.banner_width - self.width:
            self.vx = -self.vx * self.bounce_damping
            self.x = max(0, min(self.banner_width - self.width, self.x))
            self.rotation_speed *= -0.8  # Change rotation direction on bounce
        
        if self.y <= 0 or self.y >= self.banner_height - self.height:
            self.vy = -self.vy * self.bounce_damping
            self.y = max(0, min(self.banner_height - self.height, self.y))
            self.rotation_speed *= -0.8
            
            # Add some randomness to prevent settling
            if abs(self.vy) < 1:
                self.vy += random.uniform(-2, 2)
    
    def draw(self, canvas, trail_opacity=0.3):
        # Draw trail
        for i, (trail_x, trail_y, trail_rot) in enumerate(self.trail[:-1]):
            opacity = trail_opacity * (i / len(self.trail))
            if opacity > 0:
                # Rotate trail image
                trail_rotated = self.angle_img.rotate(trail_rot, expand=False)
                
                # Apply opacity
                trail_array = np.array(trail_rotated)
                if trail_array.shape[2] == 4:
                    trail_array[:, :, 3] = (trail_array[:, :, 3] * opacity).astype(np.uint8)
                trail_with_alpha = Image.fromarray(trail_array)
                
                canvas.paste(trail_with_alpha, (int(trail_x), int(trail_y)), trail_with_alpha)
        
        # Draw current position
        rotated = self.angle_img.rotate(self.rotation, expand=False)
        canvas.paste(rotated, (int(self.x), int(self.y)), rotated)

def create_bouncing_banner():
    # Load the angle element
    angle_img = Image.open('angle.png').convert('RGBA')
    print(f"Loaded angle.png: {angle_img.size}")
    
    # Discord banner dimensions
    banner_width = 600
    banner_height = 240
    
    # Animation parameters
    total_frames = 300  # 10 seconds at 30fps for longer physics simulation
    fps = 30
    bg_color = (0x16, 0x16, 0x16)
    
    # Create bouncing angles with different sizes and initial conditions
    angles = []
    
    # Large angle
    angles.append(BouncingAngle(
        x=100, y=50, vx=3, vy=1, size=60, 
        banner_width=banner_width, banner_height=banner_height, angle_img=angle_img
    ))
    
    # Medium angle
    angles.append(BouncingAngle(
        x=300, y=80, vx=-2, vy=2, size=45,
        banner_width=banner_width, banner_height=banner_height, angle_img=angle_img
    ))
    
    # Small angle
    angles.append(BouncingAngle(
        x=500, y=120, vx=-4, vy=-1, size=35,
        banner_width=banner_width, banner_height=banner_height, angle_img=angle_img
    ))
    
    # Extra small angle
    angles.append(BouncingAngle(
        x=200, y=160, vx=2.5, vy=-3, size=25,
        banner_width=banner_width, banner_height=banner_height, angle_img=angle_img
    ))
    
    frames = []
    
    for frame_num in range(total_frames):
        # Create canvas
        canvas = Image.new('RGBA', (banner_width, banner_height), bg_color + (255,))
        
        # Update and draw each angle
        for angle in angles:
            angle.update()
            angle.draw(canvas, trail_opacity=0.4)
        
        # Add subtle background gradient for depth
        gradient_overlay = Image.new('RGBA', (banner_width, banner_height), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient_overlay)
        
        for y in range(banner_height):
            alpha = int(20 * (y / banner_height))  # Subtle darkening towards bottom
            gradient_draw.rectangle([(0, y), (banner_width, y+1)], fill=(0, 0, 0, alpha))
        
        canvas = Image.alpha_composite(canvas, gradient_overlay)
        
        # Convert to RGB for GIF
        final_frame = Image.new('RGB', (banner_width, banner_height), bg_color)
        final_frame.paste(canvas, (0, 0), canvas)
        
        frames.append(final_frame)
        
        if frame_num % 30 == 0:  # Print every second
            print(f"Generated frame {frame_num + 1}/{total_frames} ({frame_num/total_frames*100:.1f}%)")
    
    # Save as animated GIF
    duration = int(1000 / fps)
    frames[0].save(
        'discord_bouncing_banner.gif',
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        optimize=True
    )
    
    print(f"Generated discord_bouncing_banner.gif - {banner_width}x{banner_height} at {fps}fps")
    print(f"Animation duration: {total_frames / fps:.1f} seconds")

if __name__ == '__main__':
    create_bouncing_banner()