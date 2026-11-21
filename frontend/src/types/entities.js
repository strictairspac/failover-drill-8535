/** @typedef {{id:number,name:string,role:'leader'|'follower',term:number,log_position:number,available:boolean,fenced:boolean,vote_enabled:boolean}} Node */
/** @typedef {{id:number,name:string,fail_primary:boolean,fence_primary:boolean,max_data_loss:number,required_term:number}} Scenario */
/** @typedef {{id:number,scenario_id:number,label:string,status:string,snapshot:object,result:object|null}} Drill */
/** @typedef {{id:number,label:string,status:'active'|'retired'|'destroyed',retain_for_recovery:boolean}} RekeyKey */
/** @typedef {{id:number,label:string,old_key_id:number,new_key_id:number,batch_size:number,tick:number,retire_at_tick:number,status:'rewriting'|'retired'}} RekeyRun */
/** @typedef {{id:number,run_id:number,page_no:number,label:string,value:string,key_id:number,version:number,rewrite_count:number}} RekeyPage */
/** @typedef {{id:number,run_id:number,name:string,role:'leader'|'follower',term:number,log_position:number,available:boolean,fenced:boolean,vote_enabled:boolean,new_key_ready:boolean,old_key_available:boolean,applied_version:number,covers_all_with_new_key:boolean}} RekeyReplica */
export {};
