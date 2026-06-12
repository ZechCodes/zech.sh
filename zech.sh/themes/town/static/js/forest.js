/* 404 / error background — an endless top-down forest. A traveller walks through it
   by day with animals roaming, then at nightfall builds a camp — lights a fire and
   sleeps inside a cabin or tent — and at dawn moves on, leaving the cold camp behind.
   Same pixel-art language as the town (world.js). */
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
  var DAYSEC=42, simT=14/24*DAYSEC;                // one day = 42s; start early afternoon so dusk/camp comes soon
  function nightLevel(h){ return 0.5+0.5*Math.cos(h/24*Math.PI*2); }
  function trailRow(x){ return 1.4*Math.sin(x*0.09) + 0.6*Math.sin(x*0.031); }   // gently winding trail (tile units)

  // ---- traveller ----
  var walker={ x:0, y:trailRow(0), facing:"right", walk:0, state:"walk", t:0 };
  var SPEED=2.3;                                   // tiles / second
  // nightly camp: shelter (house/tent) + campfire, left behind to scroll off after the night
  var camp={ x:0, y:0, fx:0, fy:0, dx:0, dy:0, type:"house", fireLit:false, shown:false, armed:false, stopX:0 };

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
  function tree(t){ var x=t.x, y=t.y, big=t.big;
    R(x+2,y+13,12,4,"rgba(0,0,0,0.22)");
    R(x+6,y+9,4,9,C.trunk);
    R(x-1-big,y-8-big,18+big*2,17+big*2,C.canOut);
    R(x+1,y-6-big,14,13+big,C.canBody);
    R(x+3,y-7-big,9,9,C.canTop);
    R(x+4,y-6,4,4,C.canHi); }
  function visibleTrees(){ var out=[], x0=Math.floor(camX/TILE)-2,x1=Math.ceil((camX+vW)/TILE)+2,y0=Math.floor(camY/TILE)-2,y1=Math.ceil((camY+vH)/TILE)+3;
    for(var ty=y0;ty<=y1;ty++)for(var tx=x0;tx<=x1;tx++) if(isTree(tx,ty)){
      var x=tx*TILE+(hash(tx,ty+101)-0.5)*9, y=ty*TILE+(hash(tx+101,ty)-0.5)*7;
      out.push({x:x, y:y, big:hash(tx,ty*3)>0.5?1:0, baseY:y+18}); }   // baseY = trunk foot, for depth sorting
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
  function campfire(wx,wy,lit){ var x=Math.round(wx-6), y=Math.round(wy-3);
    R(x,y+5,12,3,"rgba(0,0,0,0.22)");                                         // ground shadow
    R(x,y+3,2,2,"#7a7670"); R(x+10,y+3,2,2,"#7a7670"); R(x+2,y+6,2,1,"#6e6a64"); R(x+8,y+6,2,1,"#6e6a64"); // stone ring
    R(x+2,y+2,8,2, lit?"#5a3a26":"#2f2a27"); R(x+4,y,4,5, lit?"#4a2e1d":"#262220");                        // crossed logs
    if(lit){ var f=Math.floor(performance.now()/110)%3;
      R(x+3,y-1,6,3,"#ff6a2c"); R(x+4,y-4-f,4,5,"#ff8a2c"); R(x+5,y-6-f,2,4,"#ffd24a"); R(x+5,y-7-f,1,2,"#fff0b0"); }
    else { R(x+3,y+1,6,2,"#3a3632"); R(x+5,y,2,1,"#55504a"); } }                                            // cold ash
  function shelter(wx,wy,type,night){ var lit=night>0.24, bx=Math.round(wx), gy=Math.round(wy);
    if(type==="tent"){
      var top=gy-20;
      R(bx-13,gy-1,26,3,"rgba(0,0,0,0.22)");
      for(var i=0;i<20;i++){ var hw=2+Math.floor(i*0.6); R(bx-hw,top+i,hw*2,1,(i&1)?"#c2673a":"#b35c30"); }
      R(bx-1,top,2,20,"#8f4a26"); R(bx-1,top-3,2,3,"#5a3620");                // ridge pole + tip
      R(bx-4,gy-10,8,10,"#241710");                                          // dark doorway
      R(bx-5,gy-11,2,11,"#8f4a26"); R(bx+3,gy-11,2,11,"#8f4a26");            // flap edges
      R(bx-5,gy-12,3,2,"#d98a55"); R(bx+2,gy-12,3,2,"#d98a55");              // rolled-back flaps
      if(lit) R(bx-3,gy-8,6,7,"rgba(255,184,96,0.4)");                        // warm glow inside
    } else {
      var x=bx-14, y=gy-22, w=28, h=22, win=lit?"#ffd683":"#2a3850";
      R(x+1,gy-1,w,3,"rgba(0,0,0,0.22)");
      R(x,y+9,w,h-9,"#b0875a"); R(x,y+h-4,w,4,"#947148");                     // log wall + base shade
      R(x+2,y+13,w-4,1,"#9c7a52"); R(x+2,y+18,w-4,1,"#9c7a52");              // log seams
      R(x-3,y,w+6,10,"#7c3b2a"); R(x-3,y,w+6,2,"#9c5238"); R(x-3,y+8,w+6,2,"#5e2c20"); // roof
      R(bx-4,y+h-11,8,11,"#5a3a26");                                          // door
      R(x+4,y+12,6,6,"#3a2c1a"); R(x+5,y+13,4,4,win);                         // window L
      R(x+w-10,y+12,6,6,"#3a2c1a"); R(x+w-9,y+13,4,4,win);                    // window R
      if(lit){ R(x+5,y+13,4,1,"#fff2c8"); R(x+w-9,y+13,4,1,"#fff2c8"); }
    } }
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
      px(-5,-5,13,7,db); px(-5,-5,13,1,ds); px(-6,-3,2,3,ds);       // body + tail
      px(8,-8,4,5,db);                                              // short neck, angled forward
      px(10,-11,6,4,db); px(15,-10,2,2,db);                        // head + snout
      px(11,-14,1,3,dd); px(14,-14,1,3,dd);                        // small antlers
      px(13,-10,1,1,"#15110d");
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
  function moveToward(tx,ty,speed,dt){ var dx=tx-walker.x, dy=ty-walker.y, d=Math.hypot(dx,dy);
    if(d<0.07){ walker.x=tx; walker.y=ty; return true; }
    var st=Math.min(d,speed*dt); walker.x+=dx/d*st; walker.y+=dy/d*st; walker.walk+=st;
    walker.facing = Math.abs(dx)>Math.abs(dy)?(dx>0?"right":"left"):(dy>0?"down":"up"); return false; }
  function updateWalker(dt,hour){
    var sleepWindow = hour>=20.4 || hour<6.3;
    if(walker.state==="walk"){
      // at dusk, pitch a camp further up the path (just off the right edge) and keep walking toward it
      if(!camp.armed && hour>=18.4 && hour<20.4){
        camp.stopX = walker.x + (vW/TILE)*0.65 + 5; var r=trailRow(camp.stopX);
        camp.x = camp.stopX-1.0; camp.y = r-2.4;             // shelter, up-left
        camp.dx = camp.stopX-1.0; camp.dy = r-2.0;           // its doorway
        camp.fx = camp.stopX+0.9; camp.fy = r-0.5;           // campfire, in front to the right
        camp.type = hash(Math.floor(simT/DAYSEC),7)<0.4?"tent":"house"; camp.fireLit=false; camp.shown=true; camp.armed=true;
      }
      if(camp.armed && walker.x>=camp.stopX){ walker.x=camp.stopX; walker.y=trailRow(camp.stopX); walker.state="arrive"; walker.t=0; walker.facing="up"; camp.fireLit=true; }  // arrive + light the fire
      else { walker.x+=SPEED*dt; walker.y=trailRow(walker.x); walker.walk+=SPEED*dt; walker.facing="right"; }
    } else if(walker.state==="arrive"){
      walker.t+=dt; if(walker.t>0.9) walker.state="enter";                                   // tend the fire, then head in
    } else if(walker.state==="enter"){
      if(moveToward(camp.dx,camp.dy,SPEED*0.75,dt)) walker.state="sleep";                      // step into the shelter
    } else if(walker.state==="sleep"){
      if(!sleepWindow) walker.state="exit";                                                    // morning — come back out
    } else if(walker.state==="exit"){
      if(moveToward(camp.stopX,trailRow(camp.stopX),SPEED*0.75,dt)){ camp.fireLit=false; walker.state="leave"; }  // out, put the fire out
    } else if(walker.state==="leave"){
      walker.x+=SPEED*dt; walker.y=trailRow(walker.x); walker.walk+=SPEED*dt; walker.facing="right";
      if(walker.x>camp.stopX+3){ camp.armed=false; walker.state="walk"; }                       // clear of camp; it stays put and scrolls off
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
    // depth layer — trees, animals, shelter, campfire and traveller drawn back-to-front by their
    // ground-contact Y, so an animal above a tree falls behind it and one below sits in front
    var ents=[];
    visibleTrees().forEach(function(t){ ents.push({y:t.baseY, d:function(){ tree(t); }}); });
    if(dayActive) animals.forEach(function(a){ if(a.init){ var fr=a.moving?(Math.floor(performance.now()/170)%2):0;
      ents.push({y:a.y*TILE+6, d:function(){ critter(a.x*TILE,a.y*TILE,a.type,fr,a.dir); }}); } });
    if(camp.shown && camp.x*TILE+90>camX && camp.x*TILE-40<camX+vW){          // cull once the left-behind camp is fully off-screen
      ents.push({y:camp.y*TILE+1, d:function(){ shelter(camp.x*TILE,camp.y*TILE,camp.type,night); }});
      ents.push({y:camp.fy*TILE+2, d:function(){ campfire(camp.fx*TILE,camp.fy*TILE,camp.fireLit); }}); }
    if(walker.state!=="sleep"){                                                // traveller hidden while inside the shelter
      var moving=walker.state!=="arrive", pf=moving?(Math.floor(walker.walk*1.4)%2===0?1:2):0;
      ents.push({y:walker.y*TILE+1, d:function(){ person(walker.x*TILE,walker.y*TILE,walker.facing,pf); }}); }
    ents.sort(function(a,b){ return a.y-b.y; });
    ents.forEach(function(e){ e.d(); });
    if(dayActive) birds.forEach(function(b){ bird(b.x*TILE,b.y*TILE,(Math.floor(performance.now()/240+b.ph)%2)===0); });  // birds fly above everything

    // night + atmosphere
    if(night>0.04){ ctx.fillStyle="rgba(14,18,42,"+(night*0.6).toFixed(3)+")"; ctx.fillRect(0,0,canvas.width,canvas.height);
      // (no moon — this is a top-down view, there's no sky)
      // warm flickering glow from the campfire while it's lit
      if(camp.fireLit){ var fl=0.5+0.5*Math.abs(Math.sin(performance.now()/170));
        light(camp.fx*TILE, camp.fy*TILE-2, 50, "rgba(255,160,70,0.7)", (0.4+0.16*fl)*Math.min(1,night/0.4)); }
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
