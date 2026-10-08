import {describe,it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor,within} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import App from './App';
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
vi.mock('cytoscape',()=>({default:()=>({layout:()=>({run:vi.fn()}),on:vi.fn(),resize:vi.fn(),destroy:vi.fn(),fit:vi.fn(),zoom:vi.fn(()=>1)})}));
globalThis.ResizeObserver=class{observe(){}disconnect(){}unobserve(){}};

const caseRow={case_id:'CASE-P',title:'P0252 · member batch',primary_entity:'P0252',case_status:'NEW',severity:'HIGH',priority_score:86.7,potential_financial_exposure:86400,payment_workflow:'MIXED',assigned_investigator:null,decision_readiness:'MORE_EVIDENCE_NEEDED'};
const act=(action:string,label:string,status:string,reason:string,missing:string[]=[])=>({action,label,status,reason,missing,evidence_refs:[]});
const policy={policy_id:'CLAIMSHIELD-SIU-V1',readiness:'INVESTIGATION_REVIEW_ELIGIBLE',case:{case_id:'CASE-P',provider_id:'P0252',case_status:'NEW',priority_score:86.7,potential_financial_exposure:86400,active_findings:84,explained_findings:0,evidence_review:{reviewed:3,open_requests:1}},
 actions:[act('request_more_evidence','Request More Evidence','ALLOWED','Open case.'),act('monitor_provider','Monitor Provider','ALLOWED','Monitoring permitted.'),act('full_investigation','Recommend Full Investigation','ALLOWED','1 active finding(s) supported by reviewed evidence.'),act('external_referral','Recommend External Referral','NEEDS_EVIDENCE','Requirements not met.',['Mandatory evidence — phoenix successor: reviewed ownership acquisition records'])],
 decisions:[],approval_available:false,approval_note:'Approval requires an authenticated supervisor.'};
function setup(path:string){const calls:{url:string;method:string;body?:any}[]=[];vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{const method=init?.method||'GET';calls.push({url,method,body:init?.body?JSON.parse(String(init.body)):undefined});
 const data=url.includes('/decisions')&&method==='POST'?{decision_id:'PD-1',status:'RECOMMENDED'}:url.includes('/policy')?policy:url.includes('/siu/queue/next-evidence')?{items:[]}:url.includes('/siu/queue')?{items:[caseRow],total:1}:url.includes('/cases/CASE-P')?{...caseRow,summary:'s',claims:[],findings:[],evidence:[],ranking:{factors:{}},allowed_transitions:[],timeline:[]}:{items:[],total:0};
 return {ok:true,json:async()=>data};}));
 render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>);return calls;}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

describe('Compliance & decisions',()=>{
 it('shows policy action statuses with reasons and missing requirements',async()=>{setup('/cases/CASE-P');fireEvent.click(await screen.findByRole('tab',{name:/Compliance & decisions/}));
  const referral=await screen.findByLabelText('Recommend External Referral');expect(within(referral).getByText('needs evidence')).toBeInTheDocument();expect(within(referral).getByText(/ownership acquisition records/)).toBeInTheDocument();
  expect(within(screen.getByLabelText('Recommend Full Investigation')).getByText('allowed')).toBeInTheDocument();expect(screen.getByText(/authenticated supervisor/)).toBeInTheDocument();});
 it('offers only eligible actions and submits a recommendation to the backend',async()=>{const calls=setup('/cases/CASE-P');fireEvent.click(await screen.findByRole('tab',{name:/Compliance & decisions/}));
  const select=await screen.findByLabelText('Decision action');expect(within(select).queryByText(/External Referral/)).toBeNull();
  fireEvent.change(select,{target:{value:'monitor_provider'}});fireEvent.change(screen.getByLabelText('Decision justification'),{target:{value:'Keep the provider under monitoring.'}});
  fireEvent.click(screen.getByRole('button',{name:/Submit recommendation/}));
  await waitFor(()=>expect(calls.some(c=>c.method==='POST'&&c.url.endsWith('/cases/CASE-P/decisions'))).toBe(true));
  expect(calls.find(c=>c.method==='POST')!.body).toEqual({action:'monitor_provider',justification:'Keep the provider under monitoring.'});});
 it('shows decision readiness in the SIU queue',async()=>{setup('/queue');expect(await screen.findByText('More evidence needed')).toBeInTheDocument();expect(screen.getByText('Decision readiness')).toBeInTheDocument();});
});
