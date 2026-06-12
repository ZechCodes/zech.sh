/* 404 / error background — an endless top-down forest. A traveller walks through it
   by day with animals roaming, then at nightfall drops a bed, sleeps, and at dawn
   picks it up and moves on. Same pixel-art language as the town (world.js). */
(function(){
  "use strict";
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var canvas = document.getElementById("forest");
  if(!canvas) return;
  var ctx = canvas.getContext("2d");
  if(!ctx){ document.body.classList.add("fallback"); return; }

  var C={ grassA:"#3f7050",grassB:"#386848",grassHi:"#4c8060",grassDot:"#2e5740",
    path:"#caa97c",path2:"#bd9b6b",pathEdge:"#a8895c",
    trunk:"#4a2e1d", canOut:"#143523",canBody:"#22512f",canTop:"#2f6b3f",canHi:"#57a468",
    bushA:"#2c5a38",bushB:"#3a7048",
    skin:"#f0c79e",skinSh:"#d8a87e",hair:"#33231b",hood:"#ff6a2c",hoodS:"#cf4f18",pants:"#2b3450",shoe:"#1c2027",
    bedR:"#b23a2e",bedRHi:"#c8493a",bedRSeam:"#9a2f25",pillow:"#eceff1",wood:"#7a4a2c",woodDk:"#5a3620" };
  var FLOWER=["#e7b6cf","#ffd166","#cfe0ff","#e2693a"];

  var TILE=16, dpr=1, PX=4, camX=0, camY=0, vW=0, vH=0, camInit=false;
  function hash(x,y){ var n=Math.imul(x|0,73856093) ^ Math.imul(y|0,19349663); n^=n>>>13; n=Math.imul(n,1274126177); return ((n^(n>>>16))>>>0)/4294967296; }

  var running=true, lastT=-1e7, FPS=30, FRAME_MS=1000/FPS;
  var DAYSEC=42, simT=8/24*DAYSEC;                 // one day = 42s; start mid-morning
  function nightLevel(h){ return 0.5+0.5*Math.cos(h/24*Math.PI*2); }
  function trailRow(x){ return 1.4*Math.sin(x*0.09) + 0.6*Math.sin(x*0.031); }   // gently winding trail (tile units)

  // ---- traveller + bed ----
  var walker={ x:0, y:trailRow(0), facing:"right", walk:0, state:"walk", t:0 };
  var SPEED=2.3;                                   // tiles / second
  var bed={ x:0, y:0, shown:false };

  // ---- animals ----
  var ATYPE=["rabbit","fox","deer","squirrel","rabbit","fox","deer"];
  var animals=ATYPE.map(function(t,i){ return {type:t, x:0,y:0,tx:0,ty:0, sp:1.1+(i%3)*0.6, dir:1, moving:false, paused:true, pauseT:0, init:false}; });
  var birds=[]; for(var bi=0;bi<3;bi++) birds.push({x:0,y:0,vx:0,vy:0,ph:bi*1.9,init:false});
  var fireflies=[]; for(var fi=0;fi<16;fi++) fireflies.push({sp:0.5+((fi*7)%10)/10, ph:fi*1.3, vx:((fi%2)?1:-1)*(2+(fi%3)), vy:((fi%3)-1)*2});

  function resize(){ dpr=Math.min(window.devicePixelRatio||1,2);
    var rect=canvas.getBoundingClientRect();
    var cw=Math.round(rect.width)||window.innerWidth, ch=Math.round(rect.height)||window.innerHeight;
    canvas.width=Math.max(1,Math.floor(cw*dpr)); canvas.height=Math.max(1,Math.floor(ch*dpr));
    var tilesTall = ch>720 ? 17 : (cw<560 ? 13 : 15);
    PX=Math.max(2, Math.round(canvas.height/(tilesTall*TILE)));
    vW=canvas.width/PX; vH=canvas.height/PX; ctx.imageSmoothingEnabled=false;
    if(!running) drawFrame(); }
  addEventListener("resize",resize); addEventListener("orientationchange",resize);
  if(window.ResizeObserver){ try{ new ResizeObserver(function(){ resize(); }).observe(canvas); }catch(e){} }

  function R(wx,wy,w,h,col){ ctx.fillStyle=col; ctx.fillRect(Math.round((wx-camX)*PX),Math.round((wy-camY)*PX),Math.ceil(w*PX),Math.ceil(h*PX)); }
  function Sp(wx,wy){ return {x:(wx-camX)*PX,y:(wy-camY)*PX}; }
  function light(wx,wy,radius,color,strength){ var s=Sp(wx,wy),r=radius*PX,g=ctx.createRadialGradient(s.x,s.y,0,s.x,s.y,r);
    g.addColorStop(0,color); g.addColorStop(1,"rgba(0,0,0,0)");
    var pa=ctx.globalAlpha; ctx.globalAlpha=strength; ctx.fillStyle=g; ctx.fillRect(s.x-r,s.y-r,r*2,r*2); ctx.globalAlpha=pa; }

  // ---- world cells ----
  function pathRowTile(tx){ return Math.round(trailRow(tx*1)); }
  function isPath(tx,ty){ return Math.abs(ty - pathRowTile(tx)) <= 1; }
  function isTree(tx,ty){ return !isPath(tx,ty) && Math.abs(ty)<30 && hash(tx,ty)>0.82; }

  function ground(){
    var x0=Math.floor(camX/TILE)-1,x1=Math.ceil((camX+vW)/TILE)+1,y0=Math.floor(camY/TILE)-1,y1=Math.ceil((camY+vH)/TILE)+1;
    for(var ty=y0;ty<=y1;ty++)for(var tx=x0;tx<=x1;tx++){ var wx=tx*TILE,wy=ty*TILE, hv=hash(tx,ty);
      if(isPath(tx,ty)){ R(wx,wy,TILE,TILE,C.path); if(hv>0.7)R(wx+4,wy+5,3,3,C.path2);
        if(ty===pathRowTile(tx)-1)R(wx,wy+TILE-1,TILE,1,C.pathEdge); if(ty===pathRowTile(tx)+1)R(wx,wy,TILE,1,C.pathEdge); }
      else{ R(wx,wy,TILE,TILE,((tx+ty)&1)?C.grassA:C.grassB);
        if(hv>0.84)R(wx+3,wy+9,2,2,C.grassDot);
        if(hv>0.6&&hv<0.66)R(wx+10,wy+4,2,2,C.grassHi);
        if(hv<0.06){R(wx+6,wy+7,1,4,C.grassHi);R(wx+8,wy+6,1,5,C.grassHi);}
        if(hv>0.93&&hv<0.945){ var fc=FLOWER[(tx*3+ty)&3]; R(wx+6,wy+8,2,2,fc); R(wx+7,wy+9,1,3,"#2e5740"); }
        if(hv>0.7&&hv<0.74){ R(wx+4,wy+8,8,5,C.bushA); R(wx+5,wy+7,6,4,C.bushB); R(wx+6,wy+7,2,2,C.canHi); } } }
  }
  function tree(tx,ty){ var x=tx*TILE+(hash(tx,ty+101)-0.5)*9, y=ty*TILE+(hash(tx+101,ty)-0.5)*7, big=hash(tx,ty*3)>0.5?1:0;
    R(x+2,y+13,12,4,"rgba(0,0,0,0.22)");
    R(x+6,y+9,4,9,C.trunk);
    R(x-1-big,y-8-big,18+big*2,17+big*2,C.canOut);
    R(x+1,y-6-big,14,13+big,C.canBody);
    R(x+3,y-7-big,9,9,C.canTop);
    R(x+4,y-6,4,4,C.canHi); }
  function visibleTrees(){ var out=[], x0=Math.floor(camX/TILE)-2,x1=Math.ceil((camX+vW)/TILE)+2,y0=Math.floor(camY/TILE)-2,y1=Math.ceil((camY+vH)/TILE)+3;
    for(var ty=y0;ty<=y1;ty++)for(var tx=x0;tx<=x1;tx++) if(isTree(tx,ty)) out.push({tx:tx,ty:ty});
    return out; }

  // ---- sprites ----
  function person(wx,wy,facing,frame){
    var x=Math.round(wx-7), y=Math.round(wy-20);
    function p(dx,dy,w,h,c){ R(x+dx,y+dy,w,h,c); }
    var sw=(frame===1)?1:(frame===2?-1:0);
    p(2,15,3,4,C.pants); p(8,15,3,4,C.pants);
    if(facing!=="up"){ p(2+(sw>0?1:0),18,3,1,C.shoe); p(8+(sw<0?-1:0),18,3,1,C.shoe); }
    p(1,8,11,8,C.hood); p(1,14,11,2,C.hoodS); p(-1,8,2,5,C.hoodS); p(12,8,2,5,C.hoodS);
    p(2,1,9,7,C.skin); p(2,6,9,1,C.skinSh);
    if(facing==="up"){ p(1,0,11,5,C.hair); } else { p(1,0,11,3,C.hair); p(1,0,2,5,C.hair); p(10,0,2,5,C.hair); }
    if(facing==="down"){ p(4,4,1,1,"#222"); p(8,4,1,1,"#222"); } else if(facing==="left"){ p(3,4,1,1,"#222"); } else if(facing==="right"){ p(9,4,1,1,"#222"); }
  }
  function minecraftBed(wx,wy){ var x=Math.round(wx-7), y=Math.round(wy-15);
    R(x+1,y+22,14,3,"rgba(0,0,0,0.22)");                       // ground shadow
    R(x-1,y+1,2,2,C.woodDk); R(x+13,y+1,2,2,C.woodDk); R(x-1,y+19,2,2,C.woodDk); R(x+13,y+19,2,2,C.woodDk); // legs
    R(x,y,14,22,C.wood);                                       // frame
    R(x+1,y+1,12,6,C.pillow); R(x+1,y+1,12,1,"#ffffff");       // pillow (head)
    R(x+1,y+8,12,13,C.bedR); R(x+1,y+8,12,2,C.bedRHi);         // red blanket + lit top edge
    R(x+6,y+8,1,13,C.bedRSeam); }
  function sleeper(wx,wy){ var x=Math.round(wx-7), y=Math.round(wy-15);
    R(x+4,y+1,6,6,C.skin); R(x+4,y+1,6,2,C.hair); R(x+4,y+6,6,1,C.skinSh);   // head on pillow
    R(x+2,y+11,10,2,C.bedRHi); }                                              // blanket bump over body
  function critter(cx,cy,type,frame,dir){
    cx=Math.round(cx); cy=Math.round(cy); var leg=frame?1:0;
    function px(ox,oy,w,h,col){ var X=dir>0?cx+ox:cx-ox-w; R(X,cy+oy,w,h,col); }
    if(type==="rabbit"){ var rb="#cdbfae",rd="#a89684",rp="#f6f0e6";
      px(-1,1,2,3+leg,rd); px(3,1,2,3-leg,rd);
      px(-3,-3,9,5,rb); px(-3,-3,9,1,rp); px(-5,-2,2,4,rb);
      px(5,-6,4,5,rb); px(5,-11,1,5,rb); px(7,-11,1,5,rb);
      px(7,-4,1,1,"#15110d"); px(-6,-1,2,2,rp);
    } else if(type==="fox"){ var fb="#d2762e",fd="#a85a1f",fw="#f0e8df";
      px(-2,1,2,3+leg,fd); px(2,1,2,3-leg,fd); px(0,1,2,3-leg,fd); px(4,1,2,3+leg,fd);
      px(-4,-3,11,5,fb); px(-4,-3,11,1,"#e8975a");
      px(-9,-5,5,7,fb); px(-10,-7,3,3,fw);
      px(6,-6,5,5,fb); px(6,-9,2,3,fb); px(9,-9,2,3,fb);
      px(9,-4,1,1,"#15110d"); px(11,-3,1,1,"#15110d");
    } else if(type==="deer"){ var db="#9a6b43",dd="#7c5230",ds="#caa074";
      px(-4,2,2,5+leg,dd); px(0,2,2,5-leg,dd); px(3,2,2,5-leg,dd); px(7,2,2,5+leg,dd);
      px(-5,-6,14,8,db); px(-5,-6,14,1,ds); px(-6,-4,2,3,ds);
      px(9,-12,4,7,db); px(11,-15,5,4,db);
      px(12,-19,1,4,dd); px(15,-19,1,4,dd); px(14,-13,1,1,"#15110d");
    } else { var sb="#a85b2e",sd="#7c431f",be="#dba463";       // squirrel
      px(-5,-13,6,15,sb); px(-4,-14,4,5,sd); px(-3,-11,2,11,be);
      px(2,-8,5,10,sb); px(3,-3,3,5,be);
      px(3,-13,6,6,sb); px(3,-14,2,2,sb); px(7,-14,2,2,sb);
      px(8,-11,1,1,"#15110d"); px(7,-6,2,3,sd); }
  }
  function bird(bx,by,flap){ bx=Math.round(bx); by=Math.round(by); var c="#3a3a46";
    R(bx-2,by+12,6,1,"rgba(0,0,0,0.14)");
    R(bx-2,by-1,5,3,c); R(bx+3,by-2,2,2,c); R(bx+5,by-1,1,1,"#e0a040");
    if(flap){ R(bx-7,by-3,6,1,c); R(bx+2,by-3,6,1,c); } else { R(bx-7,by+1,6,1,c); R(bx+2,by+1,6,1,c); } }

  // ---- updates ----
  function updateWalker(dt,hour){
    var sleepWindow = hour>=20.4 || hour<6.3;
    if(walker.state==="walk"){
      if(sleepWindow){ walker.state="settle"; walker.t=0; bed.x=walker.x; bed.y=walker.y; bed.shown=true; walker.facing="down"; }
      else { walker.x+=SPEED*dt; walker.y=trailRow(walker.x); walker.walk+=SPEED*dt; walker.facing="right"; }
    } else if(walker.state==="settle"){
      walker.t+=dt; if(walker.t>1.3) walker.state="sleep";
    } else if(walker.state==="sleep"){
      if(!sleepWindow){ walker.state="wake"; walker.t=0; walker.facing="down"; }
    } else if(walker.state==="wake"){
      walker.t+=dt; if(walker.t>1.0){ bed.shown=false; walker.state="walk"; }
    }
  }
  function updateAnimals(dt,hour,now){
    var dayActive = hour>=6 && hour<20, cx=camX/TILE, cw=vW/TILE, cyT=camY/TILE, ch=vH/TILE;
    animals.forEach(function(a){
      if(!a.init || a.x < cx-3){ a.x=cx+cw*(0.4+hash((a.x|0)*7+1,a.type.length)*0.9); a.y=cyT+ch*(0.12+hash((a.y|0)*5+3,2)*0.76); a.tx=a.x; a.ty=a.y; a.init=true; a.paused=true; a.pauseT=0; return; }
      if(!dayActive) return;                                  // animals tucked away at night
      if(a.paused){ a.moving=false; a.pauseT-=dt;
        if(a.pauseT<=0){ a.paused=false; var rad=2.6+hash((a.x*9)|0,(a.y*9)|0)*3.2;
          a.tx=a.x+(hash((a.x*13)|0,3)*2-1)*rad; a.ty=Math.max(cyT-2, Math.min(cyT+ch+2, a.y+(hash(5,(a.y*13)|0)*2-1)*2.2)); } }
      else { var dx=a.tx-a.x, dy=a.ty-a.y, d=Math.hypot(dx,dy);
        if(d<0.2){ a.moving=false; a.paused=true; a.pauseT=0.6+hash((a.x*3)|0,(a.y*3)|0)*2.4; }
        else { var st=Math.min(d,a.sp*dt); a.x+=dx/d*st; a.y+=dy/d*st; a.dir=dx>=0?1:-1; a.moving=true; } } });
    birds.forEach(function(b){
      if(!b.init || b.x < cx-4){ b.x=cx+cw*(0.5+hash((b.x|0)+7,9)*0.8); b.y=cyT+ch*(0.05+hash((b.y|0)+2,4)*0.4); b.vx=2+hash((b.x|0),1)*2; b.vy=(hash((b.y|0),2)-0.5); b.init=true; return; }
      if(!dayActive) return; b.x+=b.vx*dt; b.y+=b.vy*dt; });
  }

  function update(dt,hour,night){
    if(!reduce){ updateWalker(dt,hour); updateAnimals(dt,hour); }
    var camTX=walker.x*TILE - vW*0.35, camTY=walker.y*TILE - vH*0.72;   // walker low-left, clear of the headline
    if(!camInit){ camX=camTX; camY=camTY; camInit=true; }
    else { camX+=(camTX-camX)*0.06; camY+=(camTY-camY)*0.06; }
  }

  function draw(hour,night){
    ctx.fillStyle=C.canBody; ctx.fillRect(0,0,canvas.width,canvas.height);    // base so no gaps
    ground();
    var dayActive = hour>=6 && hour<20;
    var trees=visibleTrees();
    trees.forEach(function(t){ if(t.ty < walker.y) tree(t.tx,t.ty); });        // behind the walker
    if(dayActive) birds.forEach(function(b){ bird(b.x*TILE,b.y*TILE,(Math.floor(performance.now()/240+b.ph)%2)===0); });
    if(dayActive) animals.forEach(function(a){ if(a.init) critter(a.x*TILE,a.y*TILE,a.type,a.moving?(Math.floor(performance.now()/170)%2):0,a.dir); });
    // bed + traveller
    if(bed.shown) minecraftBed(bed.x*TILE,bed.y*TILE);
    if(walker.state==="sleep"){ sleeper(bed.x*TILE,bed.y*TILE); }
    else { var pf=walker.state==="walk"?(Math.floor(walker.walk*1.4)%2===0?1:2):0; person(walker.x*TILE,walker.y*TILE,walker.facing,pf); }
    trees.forEach(function(t){ if(t.ty >= walker.y) tree(t.tx,t.ty); });        // in front of the walker

    // night + atmosphere
    if(night>0.04){ ctx.fillStyle="rgba(14,18,42,"+(night*0.6).toFixed(3)+")"; ctx.fillRect(0,0,canvas.width,canvas.height);
      // moon
      if(night>0.45){ var mo=Sp(camX/1+vW*0.82, camY/1+vH*0.16); var mr=7*PX;
        ctx.fillStyle="rgba(225,232,245,"+((night-0.45)/0.55).toFixed(3)+")"; ctx.beginPath(); ctx.arc(canvas.width*0.82,canvas.height*0.16,mr,0,6.3); ctx.fill(); }
      // warm lantern glow at the camp while resting
      if((walker.state==="sleep"||walker.state==="settle") && bed.shown){ light(bed.x*TILE+10, bed.y*TILE-6, 46, "rgba(255,170,90,0.6)", 0.5*Math.min(1,night/0.5)); }
      // fireflies deep in the night
      if(night>0.6){ var tff=performance.now()/1000, fAmt=Math.min(1,(night-0.6)/0.3);
        for(var k=0;k<fireflies.length;k++){ var f=fireflies[k], camCx=camX+vW/2, camCy=camY+vH/2;
          if(f.wx===undefined||Math.abs(f.wx-camCx)>vW*0.6||Math.abs(f.wy-camCy)>vH*0.6){ f.wx=camX+Math.random()*vW; f.wy=camY+Math.random()*vH; f.ph=Math.random()*6.28; }
          if(!reduce){ f.wx+=(f.vx+Math.sin(tff*f.sp+f.ph)*4)*0.033; f.wy+=(f.vy+Math.cos(tff*f.sp*0.9+f.ph)*3)*0.033; }
          var s=Sp(f.wx,f.wy); if(s.x<-30||s.x>canvas.width+30||s.y<-30||s.y>canvas.height+30) continue;
          var pl=0.3+0.7*(0.5+0.5*Math.sin(tff*2.3+f.ph)), fg=ctx.createRadialGradient(s.x,s.y,0,s.x,s.y,7*PX);
          fg.addColorStop(0,"rgba(190,255,140,"+(fAmt*pl*0.8).toFixed(3)+")"); fg.addColorStop(1,"rgba(190,255,140,0)");
          ctx.fillStyle=fg; ctx.fillRect(s.x-7*PX,s.y-7*PX,14*PX,14*PX); } }
    }
    // dawn / dusk warm wash
    var warm = Math.max(0, 0.5-Math.abs(((hour+24)%24)-6.6)/3) + Math.max(0, 0.5-Math.abs(hour-19)/3);
    if(warm>0.02){ ctx.fillStyle="rgba(255,150,70,"+(warm*0.22).toFixed(3)+")"; ctx.fillRect(0,0,canvas.width,canvas.height); }
    // vignette
    var vg=ctx.createRadialGradient(canvas.width*0.5,canvas.height*0.45,canvas.height*0.3,canvas.width*0.5,canvas.height*0.45,canvas.height*0.9);
    vg.addColorStop(0,"rgba(0,0,0,0)"); vg.addColorStop(1,"rgba(0,0,0,0.42)"); ctx.fillStyle=vg; ctx.fillRect(0,0,canvas.width,canvas.height);
  }

  function drawFrame(){ lastT=-1e7; render(); }
  function render(){
    if(running) requestAnimationFrame(render);
    var now=performance.now(), elapsed=now-lastT;
    if(elapsed<FRAME_MS) return;
    lastT=now-(elapsed%FRAME_MS);
    var dt=Math.min(0.05, elapsed/1000);
    if(!reduce) simT+=dt;
    var phase=((simT/DAYSEC)%1+1)%1, hour=phase*24, night=nightLevel(hour);
    update(dt,hour,night);
    draw(hour,night);
  }

  document.addEventListener("visibilitychange",function(){ running=!document.hidden; if(running){ lastT=0; requestAnimationFrame(render); } });

  resize();
  if(reduce){                                  // reduced motion: one calm daytime frame
    simT=10/24*DAYSEC; walker.state="walk"; walker.x=0; walker.y=trailRow(0);
    var cx=walker.x+ (vW/TILE)*0.55, cyT=walker.y;
    animals[0].init=true; animals[0].type="deer"; animals[0].x=cx; animals[0].y=cyT-3; animals[0].dir=-1;
    animals[1].init=true; animals[1].type="rabbit"; animals[1].x=walker.x+5; animals[1].y=cyT+3; animals[1].dir=1;
    animals[2].init=true; animals[2].type="fox"; animals[2].x=cx-4; animals[2].y=cyT+4; animals[2].dir=-1;
    birds[0].init=true; birds[0].x=cx; birds[0].y=cyT-5;
    running=false; render();
    return;
  }
  requestAnimationFrame(render);
})();
