(function(){
  "use strict";
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // home page uses #game (full sim); content-page banners use #town-banner (one static frame)
  var canvas=document.getElementById("game") || document.getElementById("town-banner");
  if(!canvas) return;
  var MODE = canvas.id==="town-banner" ? "banner" : "home";
  var ctx=canvas.getContext("2d");
  if(!ctx){ document.body.classList.add("fallback"); return; }

  var C={ grassA:"#3f7050",grassB:"#386848",grassHi:"#4c8060",grassDot:"#2e5740",
    path:"#caa97c",path2:"#bd9b6b",pathEdge:"#a8895c", water:"#356a86",water2:"#3f7c9c",
    trunk:"#5a3b2a",leafA:"#2f6040",leafB:"#3d7050",leafHi:"#54855f",
    wall:"#ddccac",wallSh:"#bba883",roofH:"#c14f38",roofHS:"#9c3f2c",roofN:"#3f5e8a",roofNS:"#32507a",door:"#5a3a26",
    win:"#2a3850",winLit:"#ffd683", floorW:"#9c7a52",floorW2:"#90704a",floorTile:"#c8cdd6",floorTile2:"#b9bfc9",
    iwall:"#efe6d2",iwallSh:"#cdbba0", bed:"#3f6e9c",bedP:"#f0ece2",deskW:"#6a4a32",deskTop:"#80603f",screen:"#1d2733",screenOn:"#ff9a52",
    shelf:"#8a6a46",shelfTop:"#9c7a52",produce:"#4caf50",produce2:"#e2693a",bakery:"#d8a24a",dairy:"#cfe0ff",register:"#bcbfc6",
    skin:"#f0c79e",skinSh:"#d8a87e",hair:"#33231b",hood:"#ff6a2c",hoodS:"#cf4f18",pants:"#2b3450",shoe:"#1c2027",
    bub:"#f4efe7",discord:"#5865f2",deploy:"#3fb950",msg:"#ff6a2c",box:"#b07d45",apple:"#e2473a",leaf:"#3fb950",cart:"#9aa0aa",tag:"#ffd166" };
  var NPCH=[["#5577bb","#42619c"],["#7aa86a","#5f8a52"],["#b06ab0","#8c4f8c"],["#d8a24a","#b07e2e"],["#56b6c2","#3f8b95"]];

  var TILE=16, pathY=18, Wt=50, Ht=32;
  // every house is the same footprint; the store stays larger
  var HOME={x:6,y:12,w:6,h:5,id:"home"}, STORE={x:30,y:8,w:12,h:9,id:"store"};
  var POND={x:21,y:21,w:5,h:3};
  var NPC_HOUSES=[{x:15,y:4,w:6,h:5},{x:23,y:3,w:6,h:5},{x:12,y:24,w:6,h:5},{x:23,y:25,w:6,h:5},{x:40,y:22,w:6,h:5}];
  var TREES=[[3,8],[4,24],[11,8],[16,12],[28,6],[34,20],[44,12],[2,20],[46,25],[24,24],[37,24],[10,25]];
  function hash(x,y){var n=(x*374761+y*668265)^0x9e3779b9;n=(n^(n>>13))*1274126177;return((n^(n>>16))>>>0)/4294967295;}

  // spots (x,y tile, zone, act, face)
  function spot(x,y,zone,act,face){ return {x:x,y:y,zone:zone,act:act,face:face||"down"}; }
  var homeDoorOut=spot(HOME.x+HOME.w/2, HOME.y+HOME.h+0.9,"out","go");
  var storeDoorOut=spot(STORE.x+STORE.w/2, STORE.y+STORE.h+0.9,"out","go");
  var BEDROOM={bx:HOME.x+0.7, by:HOME.y+1.5, bw:2.1, bh:1.5};
  var ZECH_BED=spot(BEDROOM.bx+1.35, BEDROOM.by+0.85,"home","sleep","up");
  var MARA_BED=spot(BEDROOM.bx+0.55, BEDROOM.by+0.85,"home","sleep","up");
  var MARA_HOME=spot(HOME.x+3.2, HOME.y+3.4,"home","idle","down");
  var OFFICE=spot(HOME.x+4.4, HOME.y+2.4,"home","code","up");  // centered under the monitor (not between it and the lamp)
  var LAMP={ x:(HOME.x+HOME.w)*TILE-42, y:HOME.y*TILE+18 };   // desk lamp (light source while coding)
  var WORK=[ spot(STORE.x+3.5,STORE.y+5,"store","work","up"), spot(STORE.x+7.5,STORE.y+4,"store","work","up"), spot(STORE.x+5.5,STORE.y+6.5,"store","work","down") ];
  var TOWN=[ {x:24,y:24},{x:33,y:24},{x:16,y:26},{x:43,y:23},{x:11,y:14} ];

  function zoneOf(x,y){
    if(x>=HOME.x&&x<HOME.x+HOME.w&&y>=HOME.y&&y<HOME.y+HOME.h) return "home";
    if(x>=STORE.x&&x<STORE.x+STORE.w&&y>=STORE.y&&y<STORE.y+STORE.h) return "store";
    return "out";
  }
  // Solid building footprints for collision. HOME/STORE are walkable for whoever
  // is entering/inside them (matched by zone); NPC houses are always solid (sims
  // only ever reach their door, never walk the interior).
  var BUILDINGS=[{r:HOME,zone:"home"},{r:STORE,zone:"store"}].concat(NPC_HOUSES.map(function(h){return {r:h,zone:null};}));
  function solid(x,y,goalZone,curZone){
    for(var i=0;i<BUILDINGS.length;i++){ var b=BUILDINGS[i],r=b.r;
      if(x>=r.x-0.1&&x<r.x+r.w+0.1&&y>=r.y-0.1&&y<r.y+r.h+0.1){
        if(b.zone&&(b.zone===goalZone||b.zone===curZone)) continue;
        return true;
      }
    }
    return false;
  }
  // ---- grid pathfinding (BFS) so sims walk AROUND buildings to the doorway ----
  function tileBlocked(tx,ty,goalZone,curZone){
    if(tx<0||ty<0||tx>=Wt||ty>=Ht) return true;
    if(tx>=POND.x&&tx<POND.x+POND.w&&ty>=POND.y&&ty<POND.y+POND.h) return true; // pond is water — walk around it
    for(var i=0;i<BUILDINGS.length;i++){ var b=BUILDINGS[i],r=b.r;
      if(tx>=r.x&&tx<r.x+r.w&&ty>=r.y&&ty<r.y+r.h){
        var dcx=Math.floor(r.x+r.w/2);
        if(ty===r.y+r.h-1&&(tx===dcx||tx===dcx-1)) return false;            // doorway is the only way in/out
        var onEdge=(tx===r.x||tx===r.x+r.w-1||ty===r.y||ty===r.y+r.h-1);
        if(onEdge) return true;                                             // perimeter walls are solid
        if(b.zone&&(b.zone===goalZone||b.zone===curZone)) return false;     // interior walkable only for whoever's entering
        return true;
      }
    }
    return false;
  }
  function findPath(sx,sy,gx,gy,goalZone,curZone){
    var s={x:Math.round(sx),y:Math.round(sy)}, g={x:Math.round(gx),y:Math.round(gy)};
    if(s.x===g.x&&s.y===g.y) return [{x:gx,y:gy}];
    var q=[s], seen={}, prev={}; seen[s.x+","+s.y]=1;
    var dirs=[[1,0],[-1,0],[0,1],[0,-1]], found=false, cnt=0;
    while(q.length&&cnt++<3000){ var c=q.shift();
      if(c.x===g.x&&c.y===g.y){ found=true; break; }
      for(var d=0;d<4;d++){ var nx=c.x+dirs[d][0], ny=c.y+dirs[d][1], k=nx+","+ny;
        if(seen[k]) continue;
        if(tileBlocked(nx,ny,goalZone,curZone)&&!(nx===g.x&&ny===g.y)) continue;
        seen[k]=1; prev[k]=c.x+","+c.y; q.push({x:nx,y:ny}); } }
    if(!found) return [{x:gx,y:gy}];
    var keys=[], ck=g.x+","+g.y;
    while(ck&&ck!==(s.x+","+s.y)){ keys.unshift(ck); ck=prev[ck]; }
    var path=keys.map(function(k){ var p=k.split(","); return {x:(+p[0])+0.5,y:(+p[1])+0.5}; });
    if(path.length) path[path.length-1]={x:gx,y:gy}; else path=[{x:gx,y:gy}];
    return path;
  }
  function planPath(a,goal){
    var cur=zoneOf(a.x,a.y), pts=[];
    if(cur!==goal.zone){
      if(cur==="home")pts.push(homeDoorOut); else if(cur==="store")pts.push(storeDoorOut);
      if(goal.zone==="home"){pts.push(homeDoorOut);pts.push(goal);}
      else if(goal.zone==="store"){pts.push(storeDoorOut);pts.push(goal);}
      else pts.push(goal);
    } else pts.push(goal);
    return pts;
  }
  function stepActor(a,goal,dt,speed){
    var key=goal.zone+":"+goal.act+":"+Math.round(goal.x*10)+","+Math.round(goal.y*10);
    if(a.goalKey!==key){ a.goalKey=key; a.path=findPath(a.x,a.y,goal.x,goal.y,goal.zone,zoneOf(a.x,a.y)); a.goal=goal; }
    if(a.path&&a.path.length){
      var p=a.path[0], dx=p.x-a.x, dy=p.y-a.y, d=Math.hypot(dx,dy)||1;
      if(d<0.2){ a.path.shift(); if(!a.path.length){ a.doing=goal.act; a.facing=goal.face; } }
      else { var st=Math.min(d,speed*dt); a.x+=dx/d*st; a.y+=dy/d*st; a.doing="walk"; a.walk+=st;
        a.facing=Math.abs(dx)>Math.abs(dy)?(dx>0?"right":"left"):(dy>0?"down":"up"); }
    } else { a.doing=goal.act; a.facing=goal.face; }
  }

  // ----- routines -----
  var activeKey="A";
  function playerGoal(h){
    if(activeKey==="A"){
      if(h<6.3) return ZECH_BED;
      if(h<17) return WORK[0];
      if(h<22) return OFFICE;
      return ZECH_BED;
    }
    // B..E : building full-time, no commute — to bed WITH Mara (~20), not after.
    // The margins became the whole day, so the late nights aren't needed anymore.
    if(h<8) return ZECH_BED;
    if(h>=20) return ZECH_BED;
    return OFFICE;
  }

  // ----- canvas -----
  var PX=4,camX=0,camY=0,vW=0,vH=0,dpr=1,camTX=0,camTY=0,camInit=false;
  function drawFrame(){ lastT=-1e7; render(); }   // force one repaint outside the loop (reduced-motion, or after a resize clears the canvas)
  function resize(){ dpr=Math.min(window.devicePixelRatio||1,2);
    // use the ACTUAL rendered box (getBoundingClientRect) so backing-store aspect always
    // matches the displayed box — prevents the canvas from stretching on mobile.
    var rect=canvas.getBoundingClientRect();
    var cw=Math.round(rect.width)||window.innerWidth, ch=Math.round(rect.height)||window.innerHeight;
    canvas.width=Math.max(1,Math.floor(cw*dpr)); canvas.height=Math.max(1,Math.floor(ch*dpr));
    var tilesTall = MODE==="banner" ? 11 : (cw<560 ? 16 : 26);   // zoom in a bit on narrow screens
    PX=Math.max(2, Math.round(canvas.height/(tilesTall*TILE)));
    vW=canvas.width/PX; vH=canvas.height/PX; ctx.imageSmoothingEnabled=false;
    if(!running) drawFrame(); }   // resizing clears the canvas — repaint the static frame when the loop isn't running
  addEventListener("resize",resize);
  addEventListener("orientationchange",resize);
  // re-resize whenever the canvas box actually changes (handles mobile toolbar show/hide)
  if(window.ResizeObserver){ try{ new ResizeObserver(function(){ resize(); }).observe(canvas); }catch(e){} }
  function R(wx,wy,w,h,col){ ctx.fillStyle=col; ctx.fillRect(Math.round((wx-camX)*PX),Math.round((wy-camY)*PX),Math.ceil(w*PX),Math.ceil(h*PX)); }
  function Sp(wx,wy){ return {x:(wx-camX)*PX,y:(wy-camY)*PX}; }

  function ground(){
    var x0=Math.floor(camX/TILE)-1,x1=Math.ceil((camX+vW)/TILE)+1,y0=Math.floor(camY/TILE)-1,y1=Math.ceil((camY+vH)/TILE)+1;
    for(var ty=y0;ty<=y1;ty++)for(var tx=x0;tx<=x1;tx++){ var wx=tx*TILE,wy=ty*TILE;
      var onPath=(ty===pathY||ty===pathY+1)||(tx>=HOME.x+HOME.w/2-1&&tx<=HOME.x+HOME.w/2+1&&ty>HOME.y+HOME.h&&ty<=pathY)||(tx>=STORE.x+STORE.w/2-1&&tx<=STORE.x+STORE.w/2+1&&ty>STORE.y+STORE.h&&ty<=pathY);
      if(onPath){ R(wx,wy,TILE,TILE,C.path); if(hash(tx,ty)>0.7)R(wx+4,wy+5,3,3,C.path2); R(wx,wy,TILE,1,C.pathEdge); }
      else{ R(wx,wy,TILE,TILE,((tx+ty)&1)?C.grassA:C.grassB); var hv=hash(tx,ty);
        if(hv>0.84)R(wx+3,wy+9,2,2,C.grassDot); if(hv>0.6&&hv<0.66)R(wx+10,wy+4,2,2,C.grassHi);
        if(hv<0.07){R(wx+6,wy+7,1,4,C.grassHi);R(wx+8,wy+6,1,5,C.grassHi);} } }
    R(POND.x*TILE,POND.y*TILE,POND.w*TILE,POND.h*TILE,C.water); R(POND.x*TILE+4,POND.y*TILE+4,POND.w*TILE-8,POND.h*TILE-9,C.water2);
  }
  function houseWindow(wx,wy,sz,lit){
    R(wx,wy,sz,sz,"#3a2c1a");                                  // frame
    R(wx+1,wy+1,sz-2,sz-2, lit?C.winLit:C.win);                // glass
    R(wx+(sz>>1),wy+1,1,sz-2,"#3a2c1a"); R(wx+1,wy+(sz>>1),sz-2,1,"#3a2c1a"); // mullions
    if(lit){ R(wx+1,wy+1,sz-2,1,"#fff2c8"); }
  }
  function npcHouse(b,lit){ var x=b.x*TILE,y=b.y*TILE,w=b.w*TILE,h=b.h*TILE;
    R(x,y+TILE,w,h-TILE,C.wall); R(x,y+h-5,w,5,C.wallSh); R(x-3,y,w+6,TILE+2,C.roofNS); R(x-1,y+1,w+2,TILE-3,C.roofN);
    R(x+w/2-5,y+h-13,10,13,C.door);
    houseWindow(x+9,y+TILE+5,14,lit); houseWindow(x+w-23,y+TILE+5,14,lit); }

  function homeClosed(lit){ var b=HOME,x=b.x*TILE,y=b.y*TILE,w=b.w*TILE,h=b.h*TILE;
    R(x,y+TILE,w,h-TILE,C.wall); R(x,y+h-6,w,6,C.wallSh); R(x-4,y,w+8,TILE+3,C.roofHS); R(x-2,y+1,w+4,TILE-2,C.roofH);
    R(x+w/2-6,y+h-16,12,16,C.door); R(x+w/2-6,y+h-16,12,2,"#3a2418");
    houseWindow(x+9,y+TILE+6,16,lit); houseWindow(x+w-25,y+TILE+6,16,lit); }
  function homeOpen(coding,sleeping){ var b=HOME,x=b.x*TILE,y=b.y*TILE,w=b.w*TILE,h=b.h*TILE;
    // floor
    for(var fy=0;fy<b.h;fy++)for(var fx=0;fx<b.w;fx++)R(x+fx*TILE,y+fy*TILE,TILE,TILE,((fx+fy)&1)?C.floorW:C.floorW2);
    // outer wall outline + divider between bedroom(left) and office(right)
    R(x,y,w,3,C.iwall); R(x,y,3,h,C.iwall); R(x+w-3,y,3,h,C.iwall); R(x,y+h-3,w,3,C.iwall);
    var divX=x+w*0.52; R(divX-1,y,3,h-12,C.iwall);
    // bedroom: double bed (Mara's side + Zech's side)
    var bdx=BEDROOM.bx*TILE,bdy=BEDROOM.by*TILE,bdw=BEDROOM.bw*TILE,bdh=BEDROOM.bh*TILE;
    R(bdx-2,bdy-1,bdw+4,bdh+4,"#5a3f28");
    R(bdx,bdy+1,bdw,bdh,C.bed); R(bdx,bdy+1,bdw,6,C.bedP); R(bdx+bdw/2-1,bdy+1,2,6,"#d8d2c4");
    // office: desk + monitor + chair
    var ox=x+w-44;
    R(ox,y+26,34,9,C.deskW); R(ox,y+26,34,3,C.deskTop);
    R(ox+10,y+15,16,12,"#2a2a30"); R(ox+12,y+17,12,9,coding?C.screenOn:C.screen); if(coding)R(ox+13,y+18,10,1,"#ffd9b0");
    R(ox+14,y+35,8,6,"#3a3a44");
    // desk lamp — warm light source when coding
    R(LAMP.x-1,LAMP.y+9,6,3,"#23262d"); R(LAMP.x+1,LAMP.y+2,2,8,"#3a3e46");
    R(LAMP.x-2,LAMP.y-2,8,5,coding?"#e8a64a":"#474b54");
    if(coding){ R(LAMP.x,LAMP.y,4,2,"#ffe6b0"); R(LAMP.x,LAMP.y-1,4,1,"#fff3d6"); }
    // door gap (bottom)
    R(x+w/2-6,y+h-3,12,3,C.floorW);
  }
  // tiny 3x5 pixel font for signage
  var FONT={S:["111","100","111","001","111"],T:["111","010","010","010","010"],O:["111","101","101","101","111"],R:["110","101","110","101","101"],E:["111","100","110","100","111"]," ":["000","000","000","000","000"]};
  function drawText(str,cx,topY,px,color){ var cw=4*px,total=str.length*cw-px,sx=cx-total/2;
    for(var i=0;i<str.length;i++){ var g=FONT[str[i]]; if(!g) continue;
      for(var r=0;r<5;r++)for(var c=0;c<3;c++) if(g[r][c]==="1") R(sx+i*cw+c*px,topY+r*px,px,px,color); } }
  function storeSign(grey){ var b=STORE,x=b.x*TILE,y=b.y*TILE,w=b.w*TILE;
    R(x+8,y-7,w-16,15,grey?"#403c36":"#1e2a38"); R(x+8,y-7,w-16,3,grey?"#7a7670":C.hood);
    drawText("STORE", x+w/2, y-3, 2, grey?"#9a9a9a":"#ffd27a"); }
  function storeClosed(grey){ var b=STORE,x=b.x*TILE,y=b.y*TILE,w=b.w*TILE,h=b.h*TILE;
    var wall=grey?"#8d8a82":"#e3d6bf", trim=grey?"#6e6a64":"#c9b896", brand=grey?"#7a7670":C.hood, glass=grey?"#3a3f44":"#bfe6d6", glassHi=grey?"#4a4f54":"#d8f2e6";
    R(x,y+12,w,h-12,wall);                                  // body
    R(x-2,y+8,w+4,8,trim); R(x-2,y+8,w+4,2,grey?"#807c74":"#efe2c8"); // parapet roof
    storeSign(grey);                                        // GROCERY sign
    var gy=y+18, gh=h-32;
    R(x+5,gy,w-10,gh,glass); R(x+5,gy,w-10,3,glassHi);      // storefront glass
    for(var i=1;i<6;i++) R(x+5+Math.round(i*(w-10)/6),gy,2,gh,wall); // mullions
    for(var a=0;a<7;a++) R(x+w/2-26+a*8,y+h-24,7,5,(a%2?"#fff":brand)); // awning
    R(x+w/2-13,y+h-18,26,18,grey?"#2f3338":"#9fd9c0"); R(x+w/2-1,y+h-18,2,18,wall); R(x+w/2-13,y+h-18,26,2,glassHi); // sliding doors
    if(!grey){ R(x+9,y+h-8,9,6,"#e2693a"); R(x+9,y+h-10,9,2,"#f08a4a"); R(x+w-18,y+h-8,9,6,"#4caf50"); R(x+w-18,y+h-10,9,2,"#5fc070"); } // produce out front
  }
  function storeOpen(){ var b=STORE,x=b.x*TILE,y=b.y*TILE,w=b.w*TILE,h=b.h*TILE;
    for(var fy=0;fy<b.h;fy++)for(var fx=0;fx<b.w;fx++)R(x+fx*TILE,y+fy*TILE,TILE,TILE,((fx+fy)&1)?C.floorTile:C.floorTile2);
    R(x,y,w,3,C.iwall); R(x,y,3,h,C.iwall); R(x+w-3,y,3,h,C.iwall); R(x,y+h-3,w,3,C.iwall);
    var ix=x+3, iy=y+3, iw=w-6, ih=h-6;
    var prod=["#e2473a","#4caf50","#ffd166","#5865f2","#e2693a","#cfe0ff","#b06ab0","#3fb950"];
    // ---- back-wall departments: produce (left) + bakery (right) ----
    R(ix+3,iy+1,iw-6,2,C.hood);                                          // signage strip
    R(ix+4,iy+3,iw/2-7,13,"#2f8f4a"); R(ix+4,iy+3,iw/2-7,3,"#39a857");
    for(var i=0;i<5;i++) R(ix+8+i*9,iy+7,5,6,(i%2?"#e2693a":"#e8c83a"));
    var bx2=ix+iw/2+3;
    R(bx2,iy+3,iw/2-7,13,"#caa15a"); R(bx2,iy+3,iw/2-7,3,"#d8b06a");
    for(var i=0;i<5;i++) R(bx2+4+i*9,iy+7,5,6,(i%2?"#b9783a":"#ecc888"));
    // ---- side-wall refrigerated cases: dairy (left), meat (right) ----
    var sy=iy+20, sh=ih-64;
    R(ix,sy,9,sh,"#6fa8bf"); R(ix,sy,9,2,"#9fd0e0");
    for(var j=0;j*13<sh-4;j++) R(ix+2,sy+4+j*13,5,8,"#e2f2f8");
    R(ix+iw-9,sy,9,sh,"#b25749"); R(ix+iw-9,sy,9,2,"#cf6f5c");
    for(var j=0;j*13<sh-4;j++) R(ix+iw-7,sy+4+j*13,5,8,"#e08a72");
    // ---- center aisles: vertical gondola shelving, front to back ----
    var AISLES=3, az0=iy+20, az1=iy+ih-46, a0=ix+15, aspan=iw-30;
    for(var a=0;a<AISLES;a++){
      var ax=a0 + Math.round((a+0.5)*aspan/AISLES) - 4;
      R(ax,az0,8,az1-az0,"#6a5a44"); R(ax,az0,8,2,"#897456"); R(ax+3,az0,2,az1-az0,"#50432e");
      for(var j=0; j*8 < az1-az0-3; j++){ R(ax-3,az0+3+j*8,3,6,prod[(a*3+j)%8]); R(ax+8,az0+3+j*8,3,6,prod[(a*5+j+2)%8]); }
    }
    // ---- checkout lanes near the entrance (dark counters + ember register; high-contrast) ----
    [-1,1].forEach(function(side){
      var cxc=x+w/2+side*42, coY=y+h-34;
      R(cxc-15,coY-1,30,12,"#3a2a1c");                                   // base shadow
      R(cxc-13,coY,26,8,"#7a5536"); R(cxc-13,coY,26,2,"#9c7042");        // wood counter + top edge
      R(cxc-11,coY+3,14,3,"#2c2f36");                                    // conveyor belt
      R(cxc+5,coY-5,8,8,"#23262d"); R(cxc+6,coY-4,6,4,"#ff9a52"); R(cxc+6,coY-4,6,1,"#ffd9b0"); // register + screen
    });
    // entrance gap
    R(x+w/2-7,y+h-3,14,3,C.floorTile);
    storeSign(false);
  }
  function tree(tx,ty){var x=tx*TILE,y=ty*TILE;
    R(x+2,y+13,12,4,"rgba(0,0,0,0.22)");   // ground shadow so it lifts off the grass
    R(x+6,y+9,4,9,"#4a2e1d");               // trunk
    R(x-1,y-8,18,17,"#143523");             // dark canopy outline
    R(x+1,y-6,14,13,"#22512f");             // canopy body (clearly darker than grass)
    R(x+3,y-7,9,9,"#2f6b3f");               // upper canopy
    R(x+4,y-6,4,4,"#57a468");               // highlight
  }
  function critter(cx,cy,type,frame,dir){
    cx=Math.round(cx); cy=Math.round(cy); var leg=frame?1:0;
    function px(ox,oy,w,h,col){ var X=dir>0?cx+ox:cx-ox-w; R(X,cy+oy,w,h,col); }
    if(type==="dog"){
      var body="#9a6438",dark="#784c28",ear="#5e3b20";
      px(-3,2,2,4,"rgba(0,0,0,0.18)"); px(6,2,2,4,"rgba(0,0,0,0.18)");
      px(-3,1,2,3+leg,dark); px(2,1,2,3-leg,dark); px(0,1,2,3-leg,dark); px(5,1,2,3+leg,dark); // 4 legs
      px(-4,-4,12,6,body); px(-4,-4,12,1,"#b07a48");                       // body + back light
      px(-6,-8,2,7,body); px(-7,-9,2,2,body);                             // tail up
      px(7,-7,6,6,body); px(12,-4,3,2,body);                              // head + snout
      px(7,-10,3,4,ear);                                                  // ear
      px(10,-5,1,1,"#15110d"); px(14,-3,1,1,"#15110d");                   // eye + nose
    } else if(type==="cat"){
      var cb="#74747e",cd="#56565f";
      px(-2,1,2,3+leg,cd); px(2,1,2,3-leg,cd); px(0,1,2,3-leg,cd); px(5,1,2,3+leg,cd);
      px(-3,-3,10,5,cb); px(-3,-3,10,1,"#8c8c96");                        // sleek body
      px(-6,-7,2,6,cb); px(-7,-9,2,3,cb);                                 // long curved tail
      px(7,-7,5,5,cb); px(7,-10,2,3,cb); px(10,-10,2,3,cb);               // head + pointy ears
      px(10,-5,1,1,"#3a5a3a"); px(12,-4,1,1,"#e090a0");                   // eye + nose
    } else { // squirrel — upright, big bushy tail
      var sb="#a85b2e",sd="#7c431f",be="#dba463";
      px(-5,-13,6,15,sb); px(-4,-14,4,5,sd); px(-3,-11,2,11,be);          // bushy tail
      px(2,-8,5,10,sb); px(3,-3,3,5,be);                                  // upright body + belly
      px(3,-13,6,6,sb); px(3,-14,2,2,sb); px(7,-14,2,2,sb);               // head + ears
      px(8,-11,1,1,"#15110d"); px(7,-6,2,3,sd);                           // eye + arm
    }
  }
  function bird(bx,by,flap){ bx=Math.round(bx); by=Math.round(by); var c="#3a3a46";
    R(bx-2,by+12,6,1,"rgba(0,0,0,0.14)");                                 // ground shadow (altitude)
    R(bx-2,by-1,5,3,c); R(bx+3,by-2,2,2,c); R(bx+5,by-1,1,1,"#e0a040");   // body + head + beak
    if(flap){ R(bx-7,by-3,6,1,c); R(bx+2,by-3,6,1,c); R(bx-7,by-2,1,1,c); R(bx+7,by-2,1,1,c); }
    else    { R(bx-7,by+1,6,1,c); R(bx+2,by+1,6,1,c); } }

  function person(wx,wy,facing,frame,palIdx,act,hairCol){
    var hd=C.hood,hs=C.hoodS; if(palIdx>0){ hd=NPCH[(palIdx-1)%NPCH.length][0]; hs=NPCH[(palIdx-1)%NPCH.length][1]; }
    var hc=hairCol||C.hair;
    var bob=(act==="code"||act==="work")?(Math.floor(Date.now()/360)%2):0;
    var sc=1.0, x=Math.round(wx-7*sc), y=Math.round(wy-20*sc-bob);
    function p(dx,dy,w,h,c){ R(x+dx*sc,y+dy*sc,w*sc,h*sc,c); }
    if(act==="sleep"){ // lying in bed: head on pillow + blanket
      p(-1,7,15,12,C.bed); p(-1,7,15,2,"#5a86b0"); p(3,1,8,7,C.skin); p(3,1,8,2,hc); p(3,6,8,1,C.skinSh); return; }
    var sw=(frame===1)?1:(frame===2?-1:0);
    p(2,15,3,4,C.pants); p(8,15,3,4,C.pants);
    if(facing!=="up"){ p(2+(sw>0?1:0),18,3,1,C.shoe); p(8+(sw<0?-1:0),18,3,1,C.shoe); }
    p(1,8,11,8,hd); p(1,14,11,2,hs); p(-1,8,2,5,hs); p(12,8,2,5,hs);
    p(2,1,9,7,C.skin); p(2,6,9,1,C.skinSh);
    if(facing==="up"){ p(1,0,11,5,C.hair); } else { p(1,0,11,3,C.hair); p(1,0,2,5,C.hair); p(10,0,2,5,C.hair); }
    if(facing==="down"){ p(4,4,1,1,"#222"); p(8,4,1,1,"#222"); } else if(facing==="left"){ p(3,4,1,1,"#222"); } else if(facing==="right"){ p(9,4,1,1,"#222"); }
  }

  // ----- task-aware bubbles -----
  var bubbles=[],lastBub=0;
  // fireflies — world-anchored, drift with the map, only in the deep of night
  var fireflies=[]; for(var ff=0;ff<26;ff++) fireflies.push({sp:0.5+((ff*7)%10)/10, ph:ff*1.3, vx:((ff%2)?1:-1)*(2+(ff%3)), vy:((ff%3)-1)*2});
  // wildlife — ground critters wander, birds fly over
  var critters=[ {type:"dog",x:30,y:28,tx:30,ty:28,sp:2.6,dir:1,moving:false,paused:false,pauseUntil:0},
                 {type:"cat",x:16,y:13,tx:16,ty:13,sp:1.9,dir:1,moving:false,paused:false,pauseUntil:0},
                 {type:"squirrel",x:35,y:19,tx:35,ty:19,sp:4.5,dir:1,moving:false,paused:false,pauseUntil:0},
                 {type:"squirrel",x:9,y:20,tx:9,ty:20,sp:4.5,dir:-1,moving:false,paused:false,pauseUntil:0} ];
  var birds=[]; for(var bi=0;bi<5;bi++) birds.push({x:8+bi*9,y:5+(bi%3)*4,vx:(bi%2?1:-1)*(2+bi%2*0.8),vy:((bi%3)-1)*0.6,ph:bi*1.7});
  var BUB_SETS={ code:["discord","github","deploy","code"], work:["box","apple","cart","tag"], sleep:["zzz"] };
  function pushBub(wx,wy,kind){ bubbles.push({x:wx,y:wy-22,born:Date.now(),kind:kind,life:2200}); }
  function icon(kind,ic,iy){
    if(kind==="discord"){R(ic,iy+1,8,5,C.discord);R(ic+1,iy+3,1,1,"#fff");R(ic+6,iy+3,1,1,"#fff");}
    else if(kind==="github"){R(ic+1,iy,6,6,"#2a2a2a");R(ic+2,iy+5,1,2,"#2a2a2a");R(ic+4,iy+5,1,2,"#2a2a2a");}
    else if(kind==="deploy"){R(ic+1,iy+3,6,1,C.deploy);R(ic+3,iy,1,6,C.deploy);R(ic+2,iy+1,3,1,C.deploy);}
    else if(kind==="code"){R(ic,iy+1,2,2,C.msg);R(ic,iy+3,2,2,C.msg);R(ic+6,iy+1,2,2,"#5865f2");R(ic+6,iy+3,2,2,"#5865f2");R(ic+3,iy,2,6,"#ddd");}
    else if(kind==="box"){R(ic+1,iy,6,6,C.box);R(ic+1,iy,6,2,"#caa06a");R(ic+3,iy,1,6,"#8a5f33");}
    else if(kind==="apple"){R(ic+1,iy+1,6,5,C.apple);R(ic+3,iy-1,1,2,C.leaf);R(ic+4,iy,2,1,C.leaf);}
    else if(kind==="cart"){R(ic,iy+1,7,4,"none");R(ic,iy+1,1,4,C.cart);R(ic,iy+4,7,1,C.cart);R(ic+6,iy+1,1,4,C.cart);R(ic+1,iy+5,1,1,C.cart);R(ic+5,iy+5,1,1,C.cart);}
    else if(kind==="tag"){R(ic,iy+1,6,5,C.tag);R(ic+5,iy+1,2,5,C.tag);R(ic+1,iy+2,1,1,"#a06a00");}
    else {R(ic,iy+1,8,4,C.msg);R(ic+2,iy+5,2,2,C.msg);}
  }
  function drawBubbles(){
    var now=Date.now();
    for(var i=bubbles.length-1;i>=0;i--){ var b=bubbles[i],age=now-b.born; if(age>b.life){bubbles.splice(i,1);continue;}
      var rise=(age/b.life)*18, bx=b.x, by=b.y-rise, a=age<200?age/200:(age>b.life-500?(b.life-age)/500:1); ctx.globalAlpha=Math.max(0,a);
      if(b.kind==="zzz"){ R(bx,by,5,1,"#cfe0ff");R(bx+2,by+1,2,1,"#cfe0ff");R(bx,by+2,5,1,"#cfe0ff"); ctx.globalAlpha=1; continue; }
      R(bx-7,by-7,14,12,C.bub); R(bx-8,by-6,1,10,C.bub); R(bx+7,by-6,1,10,C.bub); R(bx-4,by+5,3,3,C.bub);
      icon(b.kind,bx-4,by-4); ctx.globalAlpha=1;
    }
  }
  function light(wx,wy,radius,color,strength){ var s=Sp(wx,wy),r=radius*PX,g=ctx.createRadialGradient(s.x,s.y,0,s.x,s.y,r);
    g.addColorStop(0,color); g.addColorStop(1,"rgba(0,0,0,0)"); ctx.globalAlpha=strength; ctx.globalCompositeOperation="lighter"; ctx.fillStyle=g;
    ctx.beginPath(); ctx.arc(s.x,s.y,r,0,Math.PI*2); ctx.fill(); ctx.globalCompositeOperation="source-over"; ctx.globalAlpha=1; }

  // ----- actors -----
  var player={x:ZECH_BED.x,y:ZECH_BED.y,facing:"down",doing:"sleep",walk:0};
  var STORE_SPOTS=[spot(STORE.x+2,STORE.y+3.6,"store","up"),spot(STORE.x+4.5,STORE.y+4.4,"store","up"),spot(STORE.x+7,STORE.y+3.6,"store","up"),spot(STORE.x+9.3,STORE.y+4.4,"store","up"),spot(STORE.x+5.6,STORE.y+5,"store","up")];
  var CHECKOUTS=[spot(STORE.x+3.4,STORE.y+6,"store","down"),spot(STORE.x+8.6,STORE.y+6,"store","down")];
  var TOWN_SPOTS=TOWN.map(function(t){return spot(t.x,t.y,"out","idle");});
  // perimeter spots around the pond (on grass, not in the water)
  var PONDSPOTS=[spot(POND.x-1.3,POND.y+1,"out","idle","right"),spot(POND.x+POND.w+1.3,POND.y+1.5,"out","idle","left"),spot(POND.x+1.5,POND.y+POND.h+1.3,"out","idle","up"),spot(POND.x+POND.w-1.5,POND.y-1.3,"out","idle","down")];
  function houseDoor(h){ return spot(h.x+h.w/2, h.y+h.h+0.9,"out","go"); }
  // a set number of townsfolk work a store stop into their route (re-rolled daily)
  var GROCERY_VISITORS=3;
  function shuffle(a){ for(var i=a.length-1;i>0;i--){ var j=(Math.random()*(i+1))|0,t=a[i];a[i]=a[j];a[j]=t; } return a; }
  function makeRoute(visitsStore){
    var stops=[];
    stops.push({type:"pond",s:PONDSPOTS[(Math.random()*PONDSPOTS.length)|0],d:3+Math.random()*3});
    var h1=NPC_HOUSES[(Math.random()*NPC_HOUSES.length)|0]; stops.push({type:"house",house:h1,door:houseDoor(h1),d:3+Math.random()*3});
    if(Math.random()<0.6) stops.push({type:"pond",s:PONDSPOTS[(Math.random()*PONDSPOTS.length)|0],d:3+Math.random()*2});
    if(Math.random()<0.5){ var h2=NPC_HOUSES[(Math.random()*NPC_HOUSES.length)|0]; stops.push({type:"house",house:h2,door:houseDoor(h2),d:3+Math.random()*3}); }
    shuffle(stops);
    if(visitsStore){   // browse an aisle, then the checkout — kept contiguous
      var aisle={type:"store",s:STORE_SPOTS[(Math.random()*STORE_SPOTS.length)|0],d:4+Math.random()*3};
      var pay={type:"store",s:CHECKOUTS[(Math.random()*CHECKOUTS.length)|0],d:2.5+Math.random()*2};
      stops.splice((Math.random()*(stops.length+1))|0, 0, aisle, pay);
    }
    return stops;
  }
  var npcs=NPC_HOUSES.slice(0,5).map(function(h,i){
    var ownDoor=houseDoor(h);
    return { x:ownDoor.x,y:ownDoor.y,facing:"down",doing:"idle",walk:0,pal:i,house:h,homeGoal:ownDoor,
             route:makeRoute(i<GROCERY_VISITORS), routeIdx:0, dwellUntil:0, inHouse:false, curHouse:null,
             wake:6.4+Math.random()*1.3, homeH:17.6+Math.random()*1.3, inside:true };
  });
  var lastDay=0;
  // Mara — partner; randomized errands by day (pond / store / popping home),
  // home in the evening, in bed at night. Re-rolled daily like everyone else.
  NPCH.push(["#e57ca0","#c25a80"]); var MARA_PAL=NPCH.length;
  function maraRoute(){
    var stops=[], n=2+(Math.random()*2|0);
    for(var i=0;i<n;i++){ var roll=Math.random();
      if(roll<0.4) stops.push({s:PONDSPOTS[(Math.random()*PONDSPOTS.length)|0], d:3+Math.random()*3});
      else if(roll<0.72){ stops.push({s:STORE_SPOTS[(Math.random()*STORE_SPOTS.length)|0], d:4+Math.random()*3}); stops.push({s:CHECKOUTS[(Math.random()*CHECKOUTS.length)|0], d:2.5+Math.random()*2}); }
      else stops.push({s:MARA_HOME, d:3+Math.random()*2});
    }
    return stops;
  }
  var mara={x:MARA_HOME.x,y:MARA_HOME.y,facing:"down",doing:"idle",walk:0,inside:false,
            route:maraRoute(), routeIdx:0, dwellUntil:0, wake:6.8, homeH:17.5, bedH:20};

  // ----- loop -----
  var DAY=46, simClock=7/24*DAY, SPEED=6.6, running=true, lastT=-1e7;  // -1e7 => first render always clears the throttle
  var FPS=30, FRAME_MS=1000/FPS;   // cap the sim to ~30fps — plenty for pixel art, roughly halves CPU
  // night = 1 at midnight (h=0/24), 0 at noon (h=12) — tracks the sim clock
  function nightLevel(h){ return 0.5 + 0.5*Math.cos(h/24*Math.PI*2); }

  function render(){
    if(running) requestAnimationFrame(render);
    var now=performance.now(), elapsed=now-lastT;
    if(elapsed<FRAME_MS) return;            // throttle to ~30fps; skip the in-between RAF ticks
    lastT=now-(elapsed%FRAME_MS);           // carry the remainder so the cadence stays even
    var dt=Math.min(0.05, elapsed/1000);
    if(!reduce) simClock+=dt;
    var hour=((simClock%DAY)/DAY)*24, night=nightLevel(hour), townAsleep=hour>=21||hour<6;

    // update player + town — skipped under reduced-motion (we draw one posed frame instead)
    if(!reduce){
    stepActor(player, MODE==="banner"?OFFICE:playerGoal(hour), dt, SPEED);  // banner: always at the desk coding
    // re-roll everyone's route at the start of each new day so the town isn't on repeat
    var dN=Math.floor(simClock/DAY);
    if(dN!==lastDay){ lastDay=dN; var order=shuffle(npcs.map(function(_,i){return i;}));
      npcs.forEach(function(n,i){ n.route=makeRoute(order.indexOf(i)<GROCERY_VISITORS); n.routeIdx=0; n.dwellUntil=0; n.inHouse=false; n.curHouse=null; n.wake=6.4+Math.random()*1.3; n.homeH=17.6+Math.random()*1.3; });
      mara.route=maraRoute(); mara.routeIdx=0; mara.dwellUntil=0; mara.wake=6.6+Math.random()*0.8; mara.homeH=17+Math.random()*1.5; }
    // update npcs — each follows a daily ROUTE of stops (dwelling at each),
    // then heads home for the night; asleep inside after dark
    var now0=Date.now();
    npcs.forEach(function(n){
      if(hour<n.wake||hour>=21){ n.inside=true; n.inHouse=false; n.curHouse=null; n.x=n.homeGoal.x; n.y=n.homeGoal.y; n.routeIdx=0; n.dwellUntil=0; return; }
      if(hour>=n.homeH){    // dusk — head home and settle inside for the night
        n.inHouse=false; n.inside=false; stepActor(n,n.homeGoal,dt,SPEED*0.8);
        if(Math.hypot(n.homeGoal.x-n.x,n.homeGoal.y-n.y)<0.4) n.inside=true;
        return;
      }
      var stop=n.route[n.routeIdx];
      if(stop.type==="house"){              // walk to the door, step INSIDE for a while, come back out
        if(n.inHouse){ if(now0>=n.dwellUntil){ n.inHouse=false; n.inside=false; n.curHouse=null; n.dwellUntil=0; n.routeIdx=(n.routeIdx+1)%n.route.length; } return; }
        n.inside=false; stepActor(n,stop.door,dt,SPEED*0.8);
        if(Math.hypot(stop.door.x-n.x,stop.door.y-n.y)<0.45){ n.inHouse=true; n.inside=true; n.curHouse=stop.house; n.x=stop.door.x; n.y=stop.door.y; n.dwellUntil=now0+stop.d*1000; }
        return;
      }
      // pond or grocery stop
      n.inside=false; var g=stop.s; stepActor(n,g,dt,SPEED*0.8);
      if(Math.hypot(g.x-n.x,g.y-n.y)<0.35){ if(n.dwellUntil===0){ n.dwellUntil=now0+stop.d*1000; } else if(now0>=n.dwellUntil){ n.routeIdx=(n.routeIdx+1)%n.route.length; n.dwellUntil=0; } }
    });
    // update Mara — errand route by day, home in the evening, in bed at night
    mara.inside=false;
    var mg;
    if(hour<mara.wake || hour>=mara.bedH){ mg=MARA_BED; }
    else if(hour>=mara.homeH){ mg=MARA_HOME; }
    else { var ms=mara.route[mara.routeIdx]; mg=ms.s;
      if(Math.hypot(mg.x-mara.x, mg.y-mara.y)<0.35){
        if(mara.dwellUntil===0){ mara.dwellUntil=now0+ms.d*1000; }
        else if(now0>=mara.dwellUntil){ mara.routeIdx=(mara.routeIdx+1)%mara.route.length; mara.dwellUntil=0; } } }
    stepActor(mara, mg, dt, SPEED*0.8);
    }

    // camera
    if(MODE==="banner"){ camTX=player.x*TILE - vW*0.30; camTY=player.y*TILE - vH*0.5;
      if(!camInit){camX=camTX;camY=camTY;camInit=true;} camX+=(camTX-camX)*0.05; camY+=(camTY-camY)*0.05; }
    else {
      var biasX=innerWidth>760?0.64:0.5;
      camTX=player.x*TILE - vW*biasX; camTY=player.y*TILE - vH*0.52;
      if(!camInit){camX=camTX;camY=camTY;camInit=true;}
      camX+=(camTX-camX)*0.08; camY+=(camTY-camY)*0.08;
    }

    var sky = night>0.5?"#1b2740":(hour<8||hour>18?"#5a4a52":"#2a4a60"); ctx.fillStyle=sky; ctx.fillRect(0,0,canvas.width,canvas.height);
    ground();
    TREES.forEach(function(t){ if(t[1]<player.y) tree(t[0],t[1]); });

    var open=zoneOf(player.x,player.y); // building the player is inside (cutaway open)
    var storeClosedNow=(activeKey!=="A");
    var lit=(hour>=18&&hour<21);

    // open building interior FIRST, so actors inside it draw on top of the floor
    if(open==="home") homeOpen(player.doing==="code", player.doing==="sleep");
    else if(open==="store") storeOpen();
    // closed buildings (skip whichever is open) — a house lights up while occupied
    function houseLit(h){ if(lit) return true; for(var k=0;k<npcs.length;k++){ if(npcs[k].inHouse&&npcs[k].curHouse===h) return true; } return false; }
    NPC_HOUSES.forEach(function(h){ npcHouse(h, houseLit(h)); });
    if(open!=="home") homeClosed(night>0.4);
    if(open!=="store") storeClosed(storeClosedNow);

    // actors on top — only if outside, or inside the same open building as the player
    function drawActor(a,pal,hairc){ if(a.inside) return; var nz=zoneOf(a.x,a.y); if(nz!=="out"&&nz!==open) return;
      var f=a.doing==="walk"?(Math.floor(a.walk*1.3)%2===0?1:2):0; person(a.x*TILE,a.y*TILE,a.facing,f,pal,a.doing,hairc); }
    npcs.forEach(function(n){ drawActor(n,n.pal+1); });
    drawActor(mara, MARA_PAL, "#7a4a30");
    // wildlife — critters roam the grass by day only (asleep at night); never over water/buildings
    if(!reduce && hour>=6 && hour<20) critters.forEach(function(c){
      if(c.paused){ c.moving=false;
        if(now0>=c.pauseUntil){ c.paused=false;
          if(c.type==="squirrel" && Math.random()<0.8){          // squirrels dart to a tree
            for(var k=0;k<8;k++){ var tr=TREES[(Math.random()*TREES.length)|0], txx=tr[0]+0.5, tyy=tr[1]+0.8;
              if(!tileBlocked(Math.round(txx),Math.round(tyy),null,null)){ c.tx=txx; c.ty=tyy; break; } }
          } else {
            for(var k=0;k<8;k++){ var rad=c.type==="squirrel"?5:3.2, nxx=c.x+(Math.random()*2-1)*rad, nyy=c.y+(Math.random()*2-1)*rad;
              if(!tileBlocked(Math.round(nxx),Math.round(nyy),null,null)){ c.tx=nxx; c.ty=nyy; break; } } } } }
      else { var dx=c.tx-c.x, dy=c.ty-c.y, d=Math.hypot(dx,dy);
        if(d<0.25){ c.moving=false; c.paused=true; c.pauseUntil=now0+(c.type==="squirrel"?700+Math.random()*1400:1800+Math.random()*3200); }
        else { var st=Math.min(d,c.sp*dt), nx=c.x+dx/d*st, ny=c.y+dy/d*st;
          if(tileBlocked(Math.round(nx),Math.round(ny),null,null)){ c.moving=false; c.paused=true; c.pauseUntil=now0+250; } // blocked → repick
          else { c.x=nx; c.y=ny; c.dir=dx>=0?1:-1; c.moving=true; } } }
      critter(c.x*TILE,c.y*TILE,c.type, c.moving?(Math.floor(now0/(c.type==="squirrel"?130:220))%2):0, c.dir); });

    // player — always drawn (it's the camera focus)
    var pf=player.doing==="walk"?(Math.floor(player.walk*1.4)%2===0?1:2):0;
    person(player.x*TILE,player.y*TILE,player.facing,pf,0,player.doing);
    TREES.forEach(function(t){ if(t[1]>=player.y) tree(t[0],t[1]); });

    // birds flying over (world-anchored; respawn near the camera)
    var bcx=camX+vW/2, bcy=camY+vH/2;
    if(!reduce && hour>=6 && hour<20) birds.forEach(function(b){ b.x+=b.vx*dt; b.y+=b.vy*dt;
      if(Math.abs(b.x*TILE-bcx)>vW*0.75||Math.abs(b.y*TILE-bcy)>vH*0.75){ b.x=(camX+Math.random()*vW)/TILE; b.y=(camY+Math.random()*vH)/TILE; b.vx=(Math.random()<0.5?-1:1)*(2+Math.random()*1.6); b.vy=(Math.random()*2-1)*0.6; }
      bird(b.x*TILE,b.y*TILE,(Math.floor(performance.now()/240+b.ph)%2)===0); });

    // night + lights
    if(night>0.05){ ctx.fillStyle="rgba(12,16,42,"+(night*0.68).toFixed(3)+")"; ctx.fillRect(0,0,canvas.width,canvas.height);
      if(player.doing==="code"){ light(LAMP.x+2,LAMP.y,56,"rgba(255,168,82,0.55)",0.46); light(LAMP.x+2,LAMP.y-1,20,"rgba(255,212,150,0.85)",0.5); }
      light((HOME.x+HOME.w/2)*TILE,(HOME.y+1)*TILE,60,"rgba(255,200,120,0.5)",night*0.4);
      if(!townAsleep) NPC_HOUSES.forEach(function(h){ if(hour>=18&&hour<21) light((h.x+h.w/2)*TILE,(h.y+1)*TILE,44,"rgba(255,210,140,0.5)",0.5); });
      // fireflies — world-anchored (drift with the map), only deep in the night
      if(night>0.65){ var tff=performance.now()/1000, camCx=camX+vW/2, camCy=camY+vH/2, mgx=vW*0.65, mgy=vH*0.65, fAmt=Math.min(1,(night-0.65)/0.3);
        for(var fk=0;fk<fireflies.length;fk++){ var f=fireflies[fk];
          if(f.wx===undefined||Math.abs(f.wx-camCx)>mgx||Math.abs(f.wy-camCy)>mgy){ f.wx=camX+Math.random()*vW; f.wy=camY+Math.random()*vH; f.ph=Math.random()*6.28; }
          f.wx+=(f.vx+Math.sin(tff*f.sp+f.ph)*4)*dt; f.wy+=(f.vy+Math.cos(tff*f.sp*0.9+f.ph)*3)*dt;
          var s=Sp(f.wx,f.wy); if(s.x<-30||s.x>canvas.width+30||s.y<-30||s.y>canvas.height+30) continue;
          var pl=0.3+0.7*(0.5+0.5*Math.sin(tff*2.3+f.ph)), fa=fAmt*pl;
          var fg=ctx.createRadialGradient(s.x,s.y,0,s.x,s.y,7*PX); fg.addColorStop(0,"rgba(190,255,140,"+(fa*0.8).toFixed(3)+")"); fg.addColorStop(1,"rgba(190,255,140,0)");
          ctx.globalCompositeOperation="lighter"; ctx.fillStyle=fg; ctx.beginPath(); ctx.arc(s.x,s.y,7*PX,0,6.28); ctx.fill();
          ctx.fillStyle="rgba(220,255,170,"+fa.toFixed(3)+")"; ctx.fillRect(s.x-PX*0.5,s.y-PX*0.5,PX,PX); ctx.globalCompositeOperation="source-over"; } }
    } else if(hour<8||hour>17){ ctx.fillStyle="rgba(255,140,60,0.10)"; ctx.fillRect(0,0,canvas.width,canvas.height); }

    // task bubbles
    var now=Date.now();
    var set = player.doing==="code"?BUB_SETS.code : player.doing==="work"?BUB_SETS.work : player.doing==="sleep"?BUB_SETS.sleep : null;
    if(set){ var gap=player.doing==="sleep"?1600:(player.doing==="code"?850:1200);
      if(now-lastBub>gap){ pushBub(player.x*TILE,player.y*TILE,set[Math.floor(now/850)%set.length]); lastBub=now; } }
    drawBubbles();

    var vg=ctx.createRadialGradient(canvas.width*0.6,canvas.height/2,canvas.height*0.3,canvas.width*0.6,canvas.height/2,canvas.height*0.85);
    vg.addColorStop(0,"rgba(0,0,0,0)"); vg.addColorStop(1,"rgba(0,0,0,0.4)"); ctx.fillStyle=vg; ctx.fillRect(0,0,canvas.width,canvas.height);

    if(MODE==="home"){ placeLabels(); updateHUD(hour,night); }
  }

  var lblHome=document.getElementById("lblHome"),lblStore=document.getElementById("lblStore"),lblYou=document.getElementById("lblYou"),lblMara=document.getElementById("lblMara");
  function setLabel(el,wx,wy,show){ var s=Sp(wx,wy); el.style.left=(s.x/dpr)+"px"; el.style.top=(s.y/dpr)+"px"; el.style.opacity=show?"1":"0"; }
  function placeLabels(){ var open=zoneOf(player.x,player.y);
    setLabel(lblHome,(HOME.x+HOME.w/2)*TILE,(HOME.y-0.2)*TILE,open!=="home");
    setLabel(lblStore,(STORE.x+STORE.w/2)*TILE,(STORE.y-0.2)*TILE, activeKey==="A"&&open!=="store");
    setLabel(lblYou,player.x*TILE,player.y*TILE-22,true);
    var mz=zoneOf(mara.x,mara.y); setLabel(lblMara,mara.x*TILE,mara.y*TILE-20, !mara.inside&&(mz==="out"||mz===open)); }

  var dayNum=document.getElementById("dayNum"),todIcon=document.getElementById("todIcon"),todName=document.getElementById("todName");
  function updateHUD(hour,night){ dayNum.textContent=1+Math.floor(simClock/DAY);
    todName.textContent=hour<6?"NIGHT":hour<8?"DAWN":hour<12?"MORNING":hour<17?"AFTERNOON":hour<20?"EVENING":"NIGHT";
    todIcon.innerHTML= night>0.5?'<circle cx="8" cy="8" r="5" fill="#cfe0ff"/><circle cx="10" cy="6" r="4" fill="#1b2740"/>':'<circle cx="8" cy="8" r="4" fill="#ffd27a"/>'; }

  var io=new IntersectionObserver(function(e){ e.forEach(function(x){ if(x.isIntersecting){ x.target.classList.add("in"); var r=x.target.getAttribute("data-routine"); if(r)activeKey=r; } }); },{threshold:0.55});
  document.querySelectorAll(".sec").forEach(function(s){ io.observe(s); });

  document.addEventListener("visibilitychange",function(){ running=!document.hidden; if(running){lastT=0; requestAnimationFrame(render);} });
  // banner: pause the loop while it's scrolled out of view (saves CPU on content pages)
  if(MODE==="banner" && window.IntersectionObserver){
    new IntersectionObserver(function(es){ es.forEach(function(e){ running=e.isIntersecting; if(running){ lastT=0; requestAnimationFrame(render); } }); },{threshold:0}).observe(canvas);
  }
  resize();
  if(reduce){                                              // reduced-motion: one deliberately posed dusk frame
    simClock=18.6/24*DAY;                                  // dusk — warm lit windows + lamplight, town still readable
    player.x=OFFICE.x; player.y=OFFICE.y; player.doing="code"; player.facing="up"; player.walk=0;
    mara.x=MARA_BED.x; mara.y=MARA_BED.y; mara.doing="sleep"; mara.facing="up"; mara.inside=false; mara.walk=0;
    npcs.forEach(function(n){ n.inside=true; });           // clear the streets for a calm, composed still
    running=false; render();
    return;
  }
  if(MODE==="banner"){ simClock=10/24*DAY; }               // banner starts mid-morning, then lives
  requestAnimationFrame(render);
})();
