import {api} from './client.js';
export const nodesAPI={list:()=>api('/nodes'),save:(data,id)=>api('/nodes'+(id?'/'+id:''),{method:id?'PUT':'POST',body:JSON.stringify(data)})};
export const scenariosAPI={list:()=>api('/scenarios'),create:data=>api('/scenarios',{method:'POST',body:JSON.stringify(data)})};
export const drillsAPI={list:()=>api('/drills'),get:id=>api('/drills/'+id),create:data=>api('/drills',{method:'POST',body:JSON.stringify(data)})};
export const rekeyAPI={
 keys:()=>api('/rekey/keys'),
 createKey:data=>api('/rekey/keys',{method:'POST',body:JSON.stringify(data)}),
 runs:()=>api('/rekey/runs'),
 get:id=>api('/rekey/runs/'+id),
 create:data=>api('/rekey/runs',{method:'POST',body:JSON.stringify(data)}),
 write:(id,pageId,value)=>api(`/rekey/runs/${id}/pages/${pageId}/write`,{method:'POST',body:JSON.stringify({value})}),
 advance:id=>api(`/rekey/runs/${id}/advance`,{method:'POST'}),
 prepare:id=>api(`/rekey/runs/${id}/batch/prepare`,{method:'POST'}),
 interrupt:id=>api(`/rekey/runs/${id}/batch/interrupt`,{method:'POST'}),
 retry:(id,batchNo)=>api(`/rekey/runs/${id}/batches/${batchNo}/retry`,{method:'POST'}),
 catchup:(id,replicaId)=>api(`/rekey/runs/${id}/catchup`+(replicaId?'?replica_id='+replicaId:''),{method:'POST'}),
 replica:(id,replicaId,data)=>api(`/rekey/runs/${id}/replicas/${replicaId}`,{method:'POST',body:JSON.stringify(data)}),
 retire:id=>api(`/rekey/runs/${id}/retire`,{method:'POST'}),
 read:(id,nodeId,pageId,recovery)=>api(`/rekey/runs/${id}/read?page_id=${pageId}`+(nodeId?'&node_id='+nodeId:'')+(recovery?'&recovery=true':'')),
 failover:(id,data)=>api(`/rekey/runs/${id}/failover`,{method:'POST',body:JSON.stringify(data)}),
 failovers:id=>api(`/rekey/runs/${id}/failovers`),
 export:id=>`/api/rekey/runs/${id}/export`
};
