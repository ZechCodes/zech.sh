// FUSION: Dense magnetic polarity particle system
(function() {
  const canvas = document.getElementById('neuralCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let width, height, particles, mouse;
  // Scale particles ~2x on high-density displays (phones, retina)
  const densityScale = Math.min(window.devicePixelRatio || 1, 2);

  function init() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
    mouse = { x: width / 2, y: height / 2 };

    // Dense count: up to 250 particles, /6000 divisor
    const count = Math.min(Math.floor((width * height) / 6000), 250);
    particles = [];

    for (let i = 0; i < count; i++) {
      // Magnetic polarity: +1 attracted, -1 repelled
      const polarity = Math.random() > 0.5 ? 1 : -1;
      // Dense sizing: radius 0.3-1.3 (scaled up on high-density displays)
      const radius = (Math.random() * 1 + 0.3) * densityScale;
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
        radius: radius,
        baseRadius: radius,
        // Attracted = cyan (hue 185-200), Repelled = lavender (hue 220-240)
        hue: polarity === 1 ? 185 + Math.random() * 15 : 220 + Math.random() * 20,
        alpha: Math.random() * 0.35 + 0.15,
        polarity: polarity,
        force: 0.00004 + Math.random() * 0.00003,
      });
    }
  }

  function animate() {
    ctx.clearRect(0, 0, width, height);

    for (let i = 0; i < particles.length; i++) {
      const p = particles[i];
      const dx = mouse.x - p.x;
      const dy = mouse.y - p.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      // Hard bounce off cursor — all particles repelled within 10px
      if (dist < 10 && dist > 0.1) {
        const bounceStrength = 3 / (dist * dist);
        p.vx += (dx / dist) * -bounceStrength;
        p.vy += (dy / dist) * -bounceStrength;
      }

      // Magnetic polarity force (attract/repel from cursor)
      if (dist < 250 && dist > 1) {
        const strength = p.force * p.polarity;
        p.vx += dx * strength;
        p.vy += dy * strength;
      }

      p.x += p.vx;
      p.y += p.vy;
      p.vx *= 0.997;
      p.vy *= 0.997;

      // Wrap
      if (p.x < 0) p.x = width;
      if (p.x > width) p.x = 0;
      if (p.y < 0) p.y = height;
      if (p.y > height) p.y = 0;

      // Draw particle
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
      ctx.fillStyle = `hsla(${p.hue}, 80%, ${p.polarity === 1 ? 65 : 60}%, ${p.alpha})`;
      ctx.fill();

      // Dense connection distance: 200px
      for (let j = i + 1; j < particles.length; j++) {
        const p2 = particles[j];
        const dx2 = p.x - p2.x;
        const dy2 = p.y - p2.y;
        const dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2);

        // Short-range repulsion between particles
        if (dist2 < 30 && dist2 > 0.1) {
          const repel = 0.5 / (dist2 * dist2);
          p.vx += (dx2 / dist2) * repel;
          p.vy += (dy2 / dist2) * repel;
          p2.vx -= (dx2 / dist2) * repel;
          p2.vy -= (dy2 / dist2) * repel;
        }

        if (dist2 < 200) {
          // Cross-polarity connections glow brighter
          const crossPolar = p.polarity !== p2.polarity;
          const baseAlpha = crossPolar ? 0.12 : 0.05;
          const alpha = (1 - dist2 / 200) * baseAlpha;
          const lineHue = crossPolar ? 195 : 210;
          const lineLightness = crossPolar ? 75 : 60;

          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(p2.x, p2.y);
          ctx.strokeStyle = `hsla(${lineHue}, 80%, ${lineLightness}%, ${alpha})`;
          ctx.lineWidth = (crossPolar ? 0.8 : 0.5) * densityScale;
          ctx.stroke();
        }
      }
    }

    requestAnimationFrame(animate);
  }

  window.addEventListener('resize', init);
  window.addEventListener('mousemove', function(e) {
    mouse.x = e.clientX;
    mouse.y = e.clientY;
  });

  document.addEventListener('DOMContentLoaded', function() {
    init();
    animate();
  });
})();
