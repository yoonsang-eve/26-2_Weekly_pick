// 방 안 가구·캐릭터 SVG (mockups/e_daydesk.html에서 가져옴)
/* ---------- 가구·캐릭터 SVG ---------- */
const skin="#f4d3b5";
function person(x,y,c,hair="#2b2240",flip=false){
  const s=flip?-1:1;
  return `<g transform="translate(${x} ${y}) scale(${s} 1)"><g class="avatar">
    <ellipse cx="0" cy="30" rx="20" ry="6" fill="rgba(28,25,23,.12)"/>
    <rect x="-17" y="0" width="34" height="30" rx="14" fill="${c}"/>
    <rect x="-17" y="0" width="34" height="30" rx="14" fill="url(#shade)"/>
    <circle cx="0" cy="-10" r="12" fill="${skin}"/>
    <path d="M-12 -12 a12 12 0 0 1 24 0 q-6 -4 -12 -3 q-7 0 -12 3z" fill="${hair}"/>
  </g></g>`;
}
function desk(x,y,c,w=110){
  return `<g transform="translate(${x} ${y})">
    <ellipse class="screen-glow" cx="0" cy="8" rx="${w*.55}" ry="26" fill="${c}" opacity=".22" filter="url(#blur)"/>
    <rect x="${-w/2}" y="0" width="${w}" height="30" rx="8" fill="#e7d5bb" stroke="#d6c0a0"/>
    <rect x="-26" y="-22" width="52" height="30" rx="4" fill="#0a0f1e" stroke="#2b3656"/>
    <rect class="screen-glow" x="-23" y="-19" width="46" height="24" rx="2" fill="${c}" opacity=".85"/>
    <g opacity=".55" fill="#0a0f1e"><rect x="-19" y="-15" width="26" height="2.5" rx="1"/><rect x="-19" y="-10" width="34" height="2.5" rx="1"/><rect x="-19" y="-5" width="20" height="2.5" rx="1"/></g>
    <rect x="-4" y="8" width="8" height="5" fill="#0a0f1e"/>
    <rect x="${w/2-26}" y="10" width="16" height="11" rx="2" fill="#fff" stroke="#d6d3d1" opacity=".95"/>
  </g>`;
}
const plant=(x,y)=>`<g transform="translate(${x} ${y})"><rect x="-8" y="0" width="16" height="14" rx="4" fill="#3b2f2a"/><circle cx="-5" cy="-4" r="7" fill="#2f855a"/><circle cx="5" cy="-6" r="8" fill="#38a169"/><circle cx="0" cy="-12" r="6" fill="#48bb78"/></g>`;
const defs=`<defs><linearGradient id="shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".18"/><stop offset="1" stop-color="#000" stop-opacity=".2"/></linearGradient><filter id="blur"><feGaussianBlur stdDeviation="8"/></filter></defs>`;

const SC={
  scr:(c)=>`${[0,1,2].map(i=>`<g transform="translate(${38+i*30} 18)"><rect width="24" height="78" rx="5" fill="#fff" stroke="#d6d3d1"/>${[0,1,2,3,4].map(j=>`<rect x="5" y="${8+j*14}" width="14" height="3" rx="1.5" fill="#e7e5e4"/><circle class="led" style="animation-delay:${(i*5+j)*.17}s" cx="19" cy="${9.5+j*14}" r="1.8" fill="${c}"/>`).join("")}</g>`).join("")}
     <g transform="translate(190 20)"><path d="M0 0 H90 L58 42 V74 H32 V42 Z" fill="rgba(94,234,212,.08)" stroke="${c}" stroke-width="1.5"/>
     <text x="45" y="-4" text-anchor="middle" font-family="JetBrains Mono" font-size="11" fill="#78716c">KOSPI+KOSDAQ</text>
     <text x="45" y="92" text-anchor="middle" font-family="JetBrains Mono" font-size="13" font-weight="700" fill="${c}">20</text></g>`,
  ana:(c)=>`${desk(150,70,c,130)}${person(150,108,c,"#3a2a1c")}${plant(262,70)}
     <g transform="translate(40 30)"><rect width="46" height="60" rx="4" fill="#d6c0a0"/>${[0,1,2,3].map(j=>`<rect x="5" y="${6+j*13}" width="${30+(j%2)*6}" height="8" rx="1.5" fill="${["#60a5fa","#fb7185","#fcd34d","#86efac"][j]}" opacity=".6"/>`).join("")}</g>`,
  deb:(c)=>`<ellipse cx="160" cy="72" rx="78" ry="34" fill="#e7d5bb" stroke="#d6c0a0"/>
     <ellipse cx="160" cy="72" rx="78" ry="34" fill="url(#shade)" opacity=".4"/>
     ${[0,1,2].map(i=>`<rect x="${128+i*22}" y="60" width="16" height="22" rx="2" fill="#fff" stroke="#d6c0a0" opacity=".95" transform="rotate(${(i-1)*8} ${136+i*22} 71)"/>`).join("")}
     ${person(60,62,"#60a5fa","#1e2a44")}${person(262,62,"#fb7185","#3a1d1d",true)}
     <g class="bubble" id="bb"><rect x="4" y="2" width="150" height="24" rx="12" fill="#2563eb"/><text id="bbt" x="79" y="18" text-anchor="middle" font-size="11" font-weight="600" fill="#fff"></text></g>
     <g class="bubble" id="br"><rect x="166" y="2" width="150" height="24" rx="12" fill="#e11d48"/><text id="brt" x="241" y="18" text-anchor="middle" font-size="11" font-weight="600" fill="#fff"></text></g>`,
  trd:(c)=>`${desk(160,64,c,170)}<g transform="translate(160 64)"><rect x="-80" y="-20" width="44" height="26" rx="3" fill="#0a0f1e" stroke="#2b3656"/><rect class="screen-glow" x="-78" y="-18" width="40" height="22" rx="2" fill="${c}" opacity=".5"/><rect x="36" y="-20" width="44" height="26" rx="3" fill="#0a0f1e" stroke="#2b3656"/><rect class="screen-glow" x="38" y="-18" width="40" height="22" rx="2" fill="${c}" opacity=".5"/></g>${person(160,104,c,"#1b1530")}`,
  // bars: [{label:"wk3", v:-5.13}, ...] 최근 주차 수익률. 0선 기준 위/아래로 그린다.
  set:(c,bars=[])=>{const bs=bars.slice(-5),mx=Math.max(5,...bs.map(b=>Math.abs(b.v))),bw=22,gap=(150-bs.length*bw)/(bs.length+1);
     return `<g transform="translate(40 18)"><rect width="170" height="84" rx="8" fill="#fff" stroke="#d6d3d1"/>
     <line x1="10" y1="44" x2="160" y2="44" stroke="#d6d3d1" stroke-dasharray="3 4"/>
     ${bs.map((b,i)=>{const h=Math.max(2,Math.abs(b.v)/mx*30),x=10+gap+i*(bw+gap);
       return `<rect x="${x}" y="${b.v>=0?44-h:44}" width="${bw}" height="${h}" rx="3" fill="${b.v>=0?"#16a34a":"#e11d48"}" opacity=".85"><title>${b.label} ${b.v>0?"+":""}${b.v}%</title></rect>
       <text x="${x+bw/2}" y="78" text-anchor="middle" font-family="JetBrains Mono" font-size="9.5" fill="#78716c">${b.label}</text>`}).join("")}
     ${bs.length?"":`<text x="85" y="48" text-anchor="middle" font-size="11" fill="#a8a29e">정산 기록 없음</text>`}</g>
     ${person(260,70,c,"#3b2a10")}${plant(300,24)}`},
  ord:(c)=>`<g transform="translate(160 60)" opacity=".8"><rect x="-26" y="-6" width="52" height="40" rx="8" fill="none" stroke="${c}" stroke-width="2"/><path d="M-15 -6 v-10 a15 15 0 0 1 30 0 v10" fill="none" stroke="${c}" stroke-width="2"/><circle cx="0" cy="12" r="4" fill="${c}"/></g>`,
};

