import {describe,it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import App from './App';
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
vi.mock('cytoscape',()=>({default:()=>({layout:()=>({run:vi.fn()}),on:vi.fn(),resize:vi.fn(),destroy:vi.fn(),fit:vi.fn(),zoom:vi.fn(()=>1)})}));
globalThis.ResizeObserver=class{observe(){}disconnect(){}unobserve(){}};

const finding={finding_id:'F-1',finding_type:'duplicate_billing',engine:'rules',entity_type:'claim',entity_id:'C1',related_claim_ids:['C1'],related_provider_ids:['P1'],related_facility_ids:['F1'],severity:'HIGH',anomaly_score_or_rule_result:1,rule_or_model_version:'v',evidence_ids:['E-1','E-2'],explanation:'Two submissions.',data_completeness:1,limitations:[],detected_at:'2026-10-08',status:'ACTIVE'};
const ev=(id:string)=>({evidence_id:id,finding_id:'F-1',source_table_or_type:'claims',source_record_id:id.replace('E-','C'),evidence_type:'duplicate_billing',observed_value:{},reference_value_or_context:{},record_timestamp:null,provenance:{},verification_status:'source_record'});
const caseData={case_id:'CASE-R',title:'P1 · duplicate billing',primary_entity:'P1',case_status:'NEW',severity:'HIGH',priority_score:70,potential_financial_exposure:100,payment_workflow:'POSTPAYMENT',assigned_investigator:null,summary:'s',claims:[],findings:[finding],evidence:[ev('E-1'),ev('E-2')],ranking:{factors:{}},allowed_transitions:[],timeline:[]};

afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe('Finding review',()=>{
 it('explains what is missing, then saves the decision and confirms the recalculation',async()=>{
  const calls:{url:string;body:any}[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{if(init?.method==='POST')calls.push({url,body:JSON.parse(String(init.body))});const data=url.endsWith('/resolve')?{finding_id:'F-1',status:'EXPLAINED',recalculated_cases:['CASE-R']}:url.includes('/cases/CASE-R')?caseData:{items:[],total:0};return {ok:true,json:async()=>data};}));
  render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter initialEntries={['/cases/CASE-R']}><App/></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button',{name:/Review this finding/}));
  const save=screen.getByRole('button',{name:'Save decision & recalculate'});
  expect(save).toBeDisabled();expect(screen.getByText(/tick at least one source record/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Select all'}));
  fireEvent.change(screen.getByLabelText('Resolution reason'),{target:{value:'Correction record explains it.'}});
  expect(save).not.toBeDisabled();fireEvent.click(save);
  await waitFor(()=>expect(calls.some(c=>c.url.endsWith('/findings/F-1/resolve'))).toBe(true));
  expect(calls[0].body).toEqual({status:'EXPLAINED',explanation:'Correction record explains it.',verified_evidence_ids:['E-1','E-2']});
  expect(await screen.findByRole('status')).toHaveTextContent('finding marked explained; priority recalculated for 1 case(s)');
 });
});
