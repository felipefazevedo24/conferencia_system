const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const project = path.resolve(__dirname, '..');
const performanceMode = process.argv.includes('--performance');
const budgetContext = process.argv.includes('--budget-context');
const fullStructureMode = process.argv.includes('--cronograma-estrutura');
const largeStructureMode = process.argv.includes('--estrutura-grande');
const cronogramaScrollMode = process.argv.includes('--cronograma-scroll');
const cronogramaMode = process.argv.includes('--cronograma') || fullStructureMode || cronogramaScrollMode;
const recognitionPreviews = process.argv.includes('--recognition-previews');
const cylinderPreviews = process.argv.includes('--cylinder-previews');
const slowPreviewMode = process.argv.includes('--slow-previews');
const previewStarted = new Map();
const sleep = ms => new Promise(r => setTimeout(r, ms));
const source = {calculated_at:'2026-09-11T10:00:00Z'};
const nodes = Array.from({length:15}, (_,i) => ({
  id:String(i+1), aux_code:i+1, code:i ? `PC-${String(i).padStart(3,'0')}` : 'CJ-7807',
  description:i ? 'Componente de montagem com descrição técnica extensa para verificar leitura e quebra de texto' : 'Conjunto principal de montagem',
  drawing_number:'DES-2026-001', quantity:2, parent_id:i?'1':null,
  child_ids:i?[]:Array.from({length:14},(_,j)=>String(j+2)), has_children:!i,
  predecessor_ids:[], path_ids:i?['1',String(i+1)]:['1'], path_labels:i?['CJ-7807']:[],
  state:['assembling','blocked','available','completed','manufacturing','not_started'][i%6],
  state_reason:'Aguardando liberação para a próxima operação de montagem', state_reason_code:'process_locked',
  operations_total:8, operations_completed:3, operations_started:1, source_status:'Em produção',
  thumbnail_url:null
}));
const fullLinks=[[1,null],[2,1],[4,1],[5,1],[6,1],[7,1],[8,2],[9,8]];
const fullStructureNodes=fullLinks.map(([id,parent])=>{
  const children=fullLinks.filter(([,p])=>p===id).map(([child])=>String(child));
  const pathIds=[id];let current=parent;
  while(current!==null){pathIds.unshift(current);current=fullLinks.find(([code])=>code===current)?.[1]??null;}
  return {...nodes[0],id:String(id),aux_code:id,code:`9961/${String(id).padStart(3,'0')}`,
    description:`Item GRV ${id}`,parent_id:parent===null?null:String(parent),child_ids:children,
    has_children:children.length>0,path_ids:pathIds.map(String),path_labels:pathIds.map(code=>`9961/${String(code).padStart(3,'0')}`),
    state:'manufacturing'};
});
const largeStructureNodes=Array.from({length:301},(_,index)=>{
  const id=index+1;
  const parent=id===1?null:id<=31?1:2+Math.floor((id-32)/9);
  const pathIds=parent===null?[1]:parent===1?[1,id]:[1,parent,id];
  const children=id===1?Array.from({length:30},(_,i)=>String(i+2)):
    id<=31?Array.from({length:9},(_,i)=>String(32+(id-2)*9+i)):[];
  return {...nodes[0],id:String(id),aux_code:id,code:`BIG/${String(id).padStart(3,'0')}`,
    description:`Componente estrutural ${id} com descrição longa para validar leitura`,
    parent_id:parent===null?null:String(parent),child_ids:children,has_children:children.length>0,
    path_ids:pathIds.map(String),path_labels:pathIds.map(code=>`BIG/${String(code).padStart(3,'0')}`),
    thumbnail_url:null};
});
const operations = Array.from({length:8}, (_,i)=>({code:String(i+1), name:['Corte e preparação de matéria-prima','Usinagem de precisão e ajuste dimensional do componente','Inspeção dimensional','Montagem do subconjunto'][i%4], sequence:i+1, finalized:i===0, locked:i===3, machine:'Centro de usinagem CNC — máquina de produção 02', started_at:null}));
let apiMode='normal';
const requests=[];
function payload(url) {
  const parts=url.pathname.split('/');
  const number=parts[4];
  const node=(largeStructureMode&&number==='7807'
    ? largeStructureNodes.find(item=>item.aux_code===Number(parts[6]))
    : fullStructureMode&&number==='9961'
    ? fullStructureNodes.find(item=>item.aux_code===Number(parts[6]))
    : nodes[Number(parts[6])-1])||nodes[1];
  if(largeStructureMode && url.pathname.endsWith('/structure'))
    return {order:{number,title:'Estrutura grande'},nodes:largeStructureNodes,roots:['1'],progress:{percentage:0,finalized_operations:0,total_operations:0},pending_count:0,current_stage:'Produção',source};
  if(fullStructureMode && url.pathname.endsWith('/structure') && number==='9961')
    return {order:{number,title:'MOLD FI500'},nodes:fullStructureNodes,roots:['1'],progress:{percentage:77,finalized_operations:77,total_operations:100},pending_count:0,current_stage:'ProduÃ§Ã£o',source};
  if(budgetContext) {
    const budgetOrders=[{number:'9958',title:'MOLD HTX900 FBE14X39CM P25MM 5V',source_status:'CONCLUÍDO',is_budget_order:true},
      {number:'9959',title:'MOLD HTX900 FC U14X39 P25 F15 5V',source_status:'APROVADO',is_budget_order:true}];
    if(url.pathname.endsWith('/search')) return [{number:'7175',title:'3CC 85-100KVA - COMPACTA'},...budgetOrders.map(o=>({...o,matched_budget_number:7175}))];
    if(url.pathname.endsWith('/dependencies')) return {selected_order_number:number,budget_number:7175,nodes:budgetOrders,edges:[],source};
    if(url.pathname.endsWith('/structure')) {
      const order=budgetOrders.find(o=>o.number===number)||{number,title:'Conjunto industrial'};
      const pieces=[['5','PB2',1],['2','PB',1],['3','CANECO',10],['6','USINAGEM FACÃO',5]].map(([id,description,quantity],i)=>({...nodes[i],id,aux_code:Number(id),code:`${number}/${id.padStart(3,'0')}`,description,quantity,
        parent_id:i?'5':null,child_ids:i?[]:['2','3','6'],has_children:!i,path_ids:i?['5',id]:['5'],path_labels:i?[`${number}/005`,`${number}/${id.padStart(3,'0')}`]:[`${number}/005`],state:'available'}));
      return {order,nodes:pieces,roots:['5'],progress:{percentage:100,finalized_operations:8,total_operations:8},pending_count:0,current_stage:'Produção',source};
    }
  }
  if(url.pathname.endsWith('/search')) return [{number:'7900',title:'Outra ordem para validar navegação',source_status:'Em produção'}];
  if(url.pathname.endsWith('/structure')) return {order:{number,title:'Conjunto industrial'},nodes:apiMode==='empty'?[]:nodes,roots:apiMode==='empty'?[]:['1'],progress:{percentage:38,finalized_operations:3,total_operations:8},pending_count:2,current_stage:'Montagem e inspeção final',source};
  if(url.pathname.endsWith('/dependencies')) return {selected_order_number:number,budget_number:null,nodes:[{number,title:'Conjunto industrial',source_status:'Em produção',is_budget_order:false}],edges:[],source};
  if(url.pathname.endsWith('/materials')) return {item_code:node.code,material_used:true,materials:[{id:'1',code:'MAT-01',item_code:node.code,description:'Aço de construção mecânica',consumption_status:'partial',required_quantity:10,consumed_quantity:5,remaining_quantity:5,available_quantity:20,unit:'kg'}]};
  if(url.pathname.endsWith('/live')) return {operations:[{code:'2',sequence:2,state:'running',pointings:[{operator_name:'Operador de teste',started_at:'2026-09-11T09:30:00Z',machine:'CNC 02',paused:false}]}],source};
  return {node,parent:null,path:[],predecessors:[],operations,drawings:[],documents:[],observations_count:0,information_origin:'GRV',document_path:null,...(slowPreviewMode?{source:{calculated_at:new Date().toISOString()}}:{})};
}
const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(url.pathname==='/producao') {
    let template=fs.readFileSync(path.join(project,'templates/producao_shell.html'),'utf8');
    let content=template.split('{% block content %}')[1].split('{% endblock %}')[0];
    content=content.replace(/src="\/producao-original\/.*?"/s, 'src="/producao-original/?os=7807"');
    res.setHeader('Content-Type','text/html; charset=utf-8');
    return res.end(`<!doctype html><html lang="pt-BR" data-theme="claro"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>*,::before,::after{box-sizing:border-box}body{margin:0}</style><link rel="stylesheet" href="/static/css/ui_erp.css"><link rel="stylesheet" href="/static/css/erp-ui.css"><link rel="stylesheet" href="/static/css/producao_shell.css"></head><body><div id="wrapper" class="toggled"><button id="mobile-menu-toggle">☰</button><aside id="sidebar-wrapper">Menu de teste</aside><div id="page-content-wrapper"><main class="content-body">${content}</main></div></div></body></html>`);
  }
  if(url.pathname.startsWith('/api/')) {
    requests.push(url.pathname);
    if(cronogramaMode && url.pathname==='/api/producao/cronograma-entregas/classificacoes') {
      res.setHeader('Content-Type','application/json');
      return res.end(JSON.stringify({classificacoes:fullStructureMode?['MOLDE']:['CMS','Moldes']}));
    }
    if(cronogramaMode && url.pathname==='/api/producao/cronograma-entregas') {
      const deliveries=cronogramaScrollMode?Array.from({length:20},(_,index)=>({orcamento:String(7100+index),versao:'',cliente:`Cliente ${index}`,descricao:`Projeto ${index}`,classificacoes:['CMS'],data_entrega:'2026-09-25',status:'Em produção',percentual:50,os:[{numero:String(9000+index),principal:true},{numero:String(9200+index),principal:false}]})):fullStructureMode?[{orcamento:'7155',versao:'',cliente:'CIDADE ENGENHARIA LTDA',descricao:'MOLD FI500',classificacoes:['MOLDE'],data_entrega:'2026-09-18',status:'Em produção',percentual:77,os:[{numero:'9961',principal:true},{numero:'9962',principal:true},{numero:'9963',principal:true}]}]:[
        {orcamento:'7222',versao:'A',cliente:'Cliente ABC',descricao:'Transportador',classificacoes:['CMS'],data_entrega:'2026-09-25',status:'Em produção',percentual:86,os:[{numero:'7900',principal:true,descricao:'Principal'},{numero:'7807/001',principal:false,descricao:'Secundária'}]},
        {orcamento:'7333',versao:'',cliente:'Cliente Moldes',descricao:'Molde',classificacoes:['Moldes'],data_entrega:'2026-09-28',status:'Concluído',percentual:100,os:[{numero:'8010',principal:true,descricao:'Molde'}]}
      ];
      const month=Number(url.searchParams.get('mes'));
      const classification=url.searchParams.get('classificacao');
      const search=(url.searchParams.get('pesquisa')||'').toLowerCase();
      const result=month===9?deliveries.filter(item=>(!classification||item.classificacoes.includes(classification))&&(!search||[item.orcamento,item.cliente,item.descricao,...item.os.map(o=>o.numero)].some(value=>value.toLowerCase().includes(search)))):[];
      res.setHeader('Content-Type','application/json');
      return res.end(JSON.stringify({periodo:{mes:month,ano:Number(url.searchParams.get('ano'))},entregas:result}));
    }
    if(performanceMode && url.pathname.endsWith('/thumbnail')) {
      res.setHeader('Cache-Control','no-store');
      if(!previewStarted.has(url.pathname))previewStarted.set(url.pathname,Date.now());
      const stillProcessing=slowPreviewMode && Date.now()-previewStarted.get(url.pathname)<3500;
      if(!url.searchParams.has('_preview') || stillProcessing) {
        res.setHeader('Content-Type','image/png');
        return res.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64'));
      }
      if(recognitionPreviews) {
        const itemCode=Number(url.pathname.split('/').at(-2));
        const sample=cylinderPreviews?'cylinder':['assembly','profile','exploded'][(itemCode-1)%3];
        const variant=url.searchParams.get('variant')==='detail'?'detail':'thumbnail';
        res.setHeader('Content-Type','image/png');
        return res.end(fs.readFileSync(path.join(__dirname,`recognition-${sample}-${variant}.png`)));
      }
      res.setHeader('Content-Type','image/svg+xml');
      return res.end(fs.readFileSync(path.join(__dirname,'isometric-preview.svg')));
    }
    res.setHeader('Content-Type','application/json');
    if(apiMode==='error'&&url.pathname.endsWith('/structure')) {res.statusCode=503;return res.end(JSON.stringify({detail:'Falha simulada na consulta'}));}
    return res.end(JSON.stringify(payload(url)));
  }
  const relative=url.pathname==='/columbia-logo.png' ? '/static/producao_original/columbia-logo.png' : url.pathname.startsWith('/producao-original/') ? url.pathname.replace('/producao-original/','/static/producao_original/') : url.pathname;
  const file=path.resolve(project,'.'+relative+(relative.endsWith('/')?'index.html':''));
  if(!file.startsWith(project+path.sep)||!fs.existsSync(file)) {res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',({'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.png':'image/png','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream');
  res.end(fs.readFileSync(file));
});
(async()=>{
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base=`http://127.0.0.1:${server.address().port}`;
  if(process.argv.includes('--serve')) {
    console.log(`Previa de Producao com dados simulados: ${base}/producao`);
    return;
  }
  const profile=path.join(require('node:os').tmpdir(),`production-tree-test-${process.pid}-${Date.now()}`);
  const chrome=spawn('C:/Program Files/Google/Chrome/Application/chrome.exe',['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{windowsHide:true,stdio:'ignore'});
  let ws;
  try {
    let port;
    for(let i=0;i<100;i++){try{port=fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0];break;}catch{await sleep(100);}}
    assert(port,'Chrome debug port');
    const tabs=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
    await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
    let id=0;const pending=new Map();const exceptions=[];
    ws.onmessage=event=>{const msg=JSON.parse(event.data);if(msg.id){const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(msg.error):p.resolve(msg.result);}else if(msg.method==='Runtime.exceptionThrown')exceptions.push(msg.params.exceptionDetails);};
    const call=(method,params={})=>new Promise((resolve,reject)=>{const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}));});
    const evaluate=async expression=>{const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw Error(JSON.stringify(result.exceptionDetails));return result.result.value;};
    const inner=code=>evaluate(`(()=>{const w=document.querySelector('iframe').contentWindow;const d=w.document;${code}})()`);
    const wait=async(code)=>{for(let i=0;i<100;i++){if(await inner(code))return;await sleep(100);}console.log('DEBUG',await inner('return {text:d.body.innerText,html:d.body.innerHTML.slice(0,1000)};'),exceptions,requests);throw Error('Timeout: '+code);};
    const click=selector=>inner(`d.querySelector(${JSON.stringify(selector)}).click();`);
    await call('Runtime.enable');await call('Page.enable');
    await call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    await call('Page.navigate',{url:base+'/producao'}); await sleep(500);
    await wait("return !!d.querySelector('.workspace') && d.querySelectorAll('.sequence-operation-card').length===8;");

    if(largeStructureMode) {
      await wait("return d.querySelectorAll('.tree-row').length===301;");
      await wait("return d.querySelectorAll('.assembly-node').length===301;");
      const bounds=await inner("const c=d.querySelector('.flow-canvas').getBoundingClientRect();const cards=[...d.querySelectorAll('.assembly-node')].map(n=>n.getBoundingClientRect());return {inside:cards.every(r=>r.top>=c.top-1&&r.bottom<=c.bottom+1&&r.left>=c.left-1&&r.right<=c.right+1),count:cards.length};");
      assert(bounds.inside,JSON.stringify(bounds));
      assert.equal(requests.filter(path=>path.endsWith('/structure')).length,1);
      assert.deepEqual(exceptions,[]);
      console.log('PASS: 301 itens e três níveis completos, sem consulta por filho (dados simulados)');
      await call('Browser.close');return;
    }

    if(cronogramaScrollMode) {
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Cronograma')).click();");
      await wait("return d.querySelectorAll('.delivery-card').length===20;");
      await inner("const list=d.querySelector('.delivery-list');const card=d.querySelectorAll('.delivery-card')[15];list.scrollTop=card.offsetTop-list.offsetTop-100;");
      await wait("return d.querySelector('.delivery-list').scrollTop>500;");
      const before=await inner("return d.querySelector('.delivery-list').scrollTop;");
      await inner("d.querySelectorAll('.delivery-card-toggle')[15].click();");
      await wait("return w.location.search.includes('os=9015') && d.querySelectorAll('.delivery-card')[15]?.querySelectorAll('.delivery-order').length===2;");
      await wait("const list=d.querySelector('.delivery-list');const card=d.querySelectorAll('.delivery-card')[15];const a=list.getBoundingClientRect(),b=card.getBoundingClientRect();return list.scrollTop>500 && b.top>=a.top && b.top<a.bottom;");
      const after=await inner("return d.querySelector('.delivery-list').scrollTop;");
      assert(Math.abs(after-before)<5,JSON.stringify({before,after}));
      await inner("d.querySelectorAll('.delivery-card')[15].querySelectorAll('.delivery-order')[1].click();");
      await wait("return w.location.search.includes('os=9215') && d.querySelector('.delivery-list')?.scrollTop>500 && d.querySelectorAll('.delivery-card')[15]?.querySelector('.delivery-card-toggle')?.getAttribute('aria-expanded')==='true';");
      assert.deepEqual(exceptions,[]);
      console.log('PASS: ORÇ permanece visível após abrir e trocar OS na lista rolada (dados simulados)');
      await call('Browser.close');return;
    }

    if(fullStructureMode) {
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Cronograma')).click();");
      await wait("return d.querySelector('.delivery-card')?.textContent.includes('7155');");
      await inner("d.querySelector('.delivery-card-toggle').click();");
      await wait("return w.location.search.includes('os=9961') && d.querySelectorAll('.delivery-order').length===3;");
      await wait("return d.querySelectorAll('.assembly-node').length===8 && d.querySelectorAll('.tree-row').length===8;");
      await wait("const canvas=d.querySelector('.flow-canvas').getBoundingClientRect();const cards=[...d.querySelectorAll('.assembly-node')].map(n=>n.getBoundingClientRect());return cards.every(r=>r.top>=canvas.top-1 && r.bottom<=canvas.bottom+1 && r.left>=canvas.left-1 && r.right<=canvas.right+1);");
      const fullScreenshot=path.join(project,'tmp_producao_layout','cronograma-estrutura-completa.png');
      fs.mkdirSync(path.dirname(fullScreenshot),{recursive:true});
      fs.writeFileSync(fullScreenshot,Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      assert(await inner("return d.querySelector('.assembly-node.selected .node-main strong')?.textContent==='9961/001';"));
      assert(await inner("return ['002','004','005','006','007','008','009'].every(code=>[...d.querySelectorAll('.assembly-node .node-main strong')].some(n=>n.textContent==='9961/'+code));"));
      assert(await inner("const root=[...d.querySelectorAll('.assembly-node')].find(n=>n.textContent.includes('9961/001')).getBoundingClientRect();const child=[...d.querySelectorAll('.assembly-node')].find(n=>n.textContent.includes('9961/002')).getBoundingClientRect();return root.top<child.top;"));
      await inner("[...d.querySelectorAll('.assembly-node')].find(n=>n.querySelector('strong')?.textContent==='9961/009').click();");
      await wait("return d.querySelector('.tree-row.selected strong')?.textContent==='9961/009' && d.querySelector('.details-panel').textContent.includes('9961/009');");
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent==='Estrutura').click();");
      await inner("[...d.querySelectorAll('.tree-row .tree-item')].find(n=>n.querySelector('strong')?.textContent==='9961/004').click();");
      await wait("return d.querySelector('.assembly-node.selected strong')?.textContent==='9961/004';");
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Cronograma')).click();");
      await inner("[...d.querySelectorAll('.delivery-order')].find(b=>b.textContent.trim().startsWith('9962')).click();");
      await wait("return w.location.search.includes('os=9962');");
      await wait("return [...d.querySelectorAll('.delivery-order')].some(b=>b.textContent.trim().startsWith('9961'));");
      await inner("[...d.querySelectorAll('.delivery-order')].find(b=>b.textContent.trim().startsWith('9961')).click();");
      await wait("return w.location.search.includes('os=9961') && d.querySelector('.assembly-node.selected strong')?.textContent==='9961/001';");
      assert.deepEqual(exceptions,[]);
      console.log('PASS: ORÇ 7155 → OS 9961 → raiz 001, descendentes, árvore e mapa sincronizados (dados simulados)');
      await call('Browser.close');return;
    }

    if(cronogramaMode) {
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Cronograma')).click();");
      await wait("return d.querySelectorAll('.delivery-card').length===2;");
      assert(await inner("return d.querySelectorAll('.tree-panel .panel-title .delivery-tabs button').length===2 && d.querySelector('.delivery-tabs button').textContent==='Estrutura' && w.getComputedStyle(d.querySelector('.tree-panel .panel-title h2')).display==='none';"));
      const screenshot=path.join(project,'tmp_producao_layout','cronograma-painel.png');
      fs.mkdirSync(path.dirname(screenshot),{recursive:true});
      fs.writeFileSync(screenshot,Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await inner("d.querySelector('.delivery-classification').value='CMS';d.querySelector('.delivery-classification').dispatchEvent(new w.Event('change',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===1 && d.querySelector('.delivery-card').textContent.includes('7222');");
      await inner("d.querySelector('.delivery-card-toggle').click();");
      await wait("return w.location.search.includes('os=7900') && d.querySelectorAll('.delivery-order').length===2;");
      await inner("[...d.querySelectorAll('.delivery-order')].find(b=>b.textContent.includes('7807/001')).click();");
      await wait("return w.location.search.includes('os=7807%2F001') && d.querySelector('.delivery-order.selected')?.textContent.includes('7807/001');");
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Estrutura')).click();");
      await wait("return d.querySelector('.tree-row.selected') && w.getComputedStyle(d.querySelector('.tree-content')).display!=='none';");
      assert(await inner("const title=d.querySelector('.tree-panel .panel-title').getBoundingClientRect();const actions=d.querySelector('.tree-panel .panel-title > div:not(.delivery-tabs)').getBoundingClientRect();return actions.right<=title.right+1;"));
      assert(await inner("return w.location.search.includes('os=7807%2F001') && !!d.querySelector('.assembly-node');"));
      await inner("[...d.querySelectorAll('.delivery-tabs button')].find(b=>b.textContent.includes('Cronograma')).click();");
      await inner("d.querySelector('.delivery-classification').value='Moldes';d.querySelector('.delivery-classification').dispatchEvent(new w.Event('change',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===1 && d.querySelector('.delivery-card').textContent.includes('7333');");
      await inner("d.querySelector('.delivery-card-toggle').click();");
      await wait("return w.location.search.includes('os=8010') && d.querySelectorAll('.delivery-order').length===1 && !!d.querySelector('.assembly-node');");
      await inner("d.querySelector('.delivery-classification').value='';d.querySelector('.delivery-classification').dispatchEvent(new w.Event('change',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===2;");
      await inner("const input=d.querySelector('.delivery-search');input.value='Cliente Moldes';input.dispatchEvent(new w.Event('input',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===1 && d.querySelector('.delivery-card').textContent.includes('7333');");
      await inner("d.querySelector('.delivery-search').value='7222';d.querySelector('.delivery-search').dispatchEvent(new w.Event('input',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===1 && d.querySelector('.delivery-card').textContent.includes('7222');");
      await inner("const input=d.querySelector('.delivery-search');input.value='7807/001';input.dispatchEvent(new w.Event('input',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===1 && d.querySelector('.delivery-card').textContent.includes('7222');");
      await inner("d.querySelector('.delivery-search').value='';d.querySelector('.delivery-search').dispatchEvent(new w.Event('input',{bubbles:true}));");
      await wait("return d.querySelectorAll('.delivery-card').length===2;");
      await inner("d.querySelector('[aria-label=\"Próximo mês\"]').click();");
      await wait("return d.querySelector('.delivery-message')?.textContent==='Nenhuma entrega prevista para este período.';");
      await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
      await click('[data-production-target="tree"]');
      await wait("return w.getComputedStyle(d.querySelector('.tree-panel')).display==='grid' && w.getComputedStyle(d.querySelector('.delivery-panel')).display==='flex';");
      assert(await inner("return d.querySelector('.delivery-panel').scrollWidth <= d.querySelector('.delivery-panel').clientWidth + 1;"));
      assert.deepEqual(exceptions,[]);
      console.log('PASS: cronograma filtra, abre orçamento e OS, preserva árvore e mapa (dados simulados)');
      await call('Browser.close');return;
    }

    if(budgetContext) {
      await inner("const input=d.querySelector('input[placeholder*=\"orçamento\"]');Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').set.call(input,'7175');input.dispatchEvent(new w.Event('input',{bubbles:true}));");
      await wait("return d.body.innerText.includes('Orçamento 7175 · OS 9958') && d.body.innerText.includes('Orçamento 7175 · OS 9959');");
      await inner("[...d.querySelectorAll('button')].find(b=>b.textContent.includes('Orçamento 7175 · OS 9958')).click();");
      await wait("return d.querySelector('.tree-row strong')?.textContent==='9958/005';");
      await inner("[...d.querySelectorAll('.map-view-switch button')].find(b=>b.textContent==='Dependências').click();");
      await wait("return !!d.querySelector('.dependency-node') && d.body.innerText.includes('Orçamento 7175');");
      await inner("[...d.querySelectorAll('.dependency-filter button')].find(b=>b.textContent==='Todas as OS').click();");
      await wait("return d.querySelectorAll('.dependency-node').length===2;");
      assert(await inner("return d.querySelectorAll('.react-flow__edge').length===0;"));
      assert(await inner("return d.querySelector('.dependency-canvas').textContent.includes('CONCLUÍDO') && d.querySelector('.dependency-canvas').textContent.includes('APROVADO');"));
      await inner("[...d.querySelectorAll('.dependency-filter button')].find(b=>b.textContent==='Cadeia da OS').click();");
      await wait("return d.querySelectorAll('.dependency-node').length===1 && d.body.innerText.includes('Esta OS não possui dependências registradas.');");
      await inner("[...d.querySelectorAll('.map-view-switch button')].find(b=>b.textContent==='Estrutura da OS').click();");
      await wait("return d.querySelectorAll('.assembly-node').length===4;");
      await wait("return d.querySelectorAll('.react-flow__edge').length===3;");
      assert(await inner("return [...d.querySelectorAll('.assembly-node')].find(n=>n.textContent.includes('9958/003')).textContent.includes('10');"));
      const artifact=path.join(project,'tmp_producao_contexto');fs.mkdirSync(artifact,{recursive:true});
      fs.writeFileSync(path.join(artifact,'estrutura.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await inner("[...d.querySelectorAll('.map-view-switch button')].find(b=>b.textContent==='Dependências').click();");
      await wait("return !!d.querySelector('.dependency-filter');");
      await inner("[...d.querySelectorAll('.dependency-filter button')].find(b=>b.textContent==='Todas as OS').click();");
      await wait("return d.querySelectorAll('.dependency-node').length===2;");
      fs.writeFileSync(path.join(artifact,'dependencias.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));
      await inner("[...d.querySelectorAll('.dependency-node')].find(b=>b.textContent.includes('OS 9959')).click();");
      await wait("return d.querySelector('.tree-row strong')?.textContent==='9959/005';");
      assert.deepEqual(exceptions,[]);
      console.log('PASS: orçamento 7175, duas OS, filtros, navegação e hierarquia das peças (dados simulados)');
      await call('Browser.close');return;
    }

    const selectLast=()=>inner("const cards=[...d.querySelectorAll('.assembly-node')];const card=cards.find(c=>c.querySelector('.node-main strong')?.textContent==='PC-014');if(!card)throw Error('Missing card');card.click();");
    await inner("d.querySelector('.tree-scroll').style.maxHeight='220px';d.querySelector('.tree-scroll').scrollTop=0;");
    await selectLast();
    await wait("const r=d.querySelector('.tree-row.selected');return r?.querySelector('strong')?.textContent==='PC-014' && d.querySelector('.tree-scroll').scrollTop>0;");
    const measure=await inner("const row=d.querySelector('.tree-row.selected');const r=row.getBoundingClientRect();const s=d.querySelector('.tree-scroll').getBoundingClientRect();return {visible:r.top>=s.top && r.bottom<=s.bottom,subtitle:w.getComputedStyle(row.querySelector('small')).display};");
    assert(measure.visible,JSON.stringify(measure));assert.equal(measure.subtitle,'block');
    console.log('PASS: map click selects and scrolls tree, with description visible');
    await click('.tree-divider button');await selectLast();
    await wait("return d.querySelector('.tree-divider button').getAttribute('aria-expanded')==='true';");
    console.log('PASS: collapsed tree reopens');
    await inner("const input=d.querySelector('.tree-search input');Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').set.call(input,'no-match');input.dispatchEvent(new w.Event('input',{bubbles:true}));");
    await wait("return !d.querySelector('.tree-row');");await selectLast();
    await wait("return d.querySelector('.tree-row.selected')?.querySelector('strong')?.textContent==='PC-014';");
    console.log('PASS: search no longer hides selected map item');
    await click('[aria-label="Filtrar estrutura"]');await inner("d.querySelector('.status-filters input').click();");
    await click('[aria-label="Filtrar estrutura"]');await selectLast();
    await wait("return d.querySelector('.tree-row.selected')?.querySelector('strong')?.textContent==='PC-014';");
    console.log('PASS: hidden status filters clear to reveal selection');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
    await sleep(300);await inner("d.querySelector('.react-flow__controls-fitview').click();");await sleep(300);await inner("d.querySelector('.assembly-node').click();");await click('[data-production-target="tree"]');
    await wait("const row=d.querySelector('.tree-row.selected');if(!row)return false;const r=row.getBoundingClientRect();const s=d.querySelector('.tree-scroll').getBoundingClientRect();return r.height>0 && r.top>=s.top && r.bottom<=s.bottom;");
    console.log('PASS: compact tree tab reveals selection');
    assert.deepEqual(exceptions,[]);await call('Browser.close');
  }finally {if(ws)ws.close();chrome.kill();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
