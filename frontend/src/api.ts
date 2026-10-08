import {useQuery} from '@tanstack/react-query';
export async function api<T=any>(path:string,body?:unknown):Promise<T>{const r=await fetch('/api/v1'+path,{method:body===undefined?'GET':'POST',headers:body===undefined?undefined:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok){const e=await r.json().catch(()=>({detail:r.statusText}));throw new Error(typeof e.detail==='string'?e.detail:JSON.stringify(e.detail));}return r.json();}
export function useApi<T=any>(path:string,refresh=false){return useQuery<T>({queryKey:[path],queryFn:()=>api<T>(path),refetchInterval:refresh?5000:false,retry:1});}
export const money=(v:unknown)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(Number(v||0));
export const num=(v:unknown)=>new Intl.NumberFormat('en-US').format(Number(v||0));
export const human=(s:string)=>s.replaceAll('_',' ').toLowerCase();
