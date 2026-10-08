import {describe,it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,cleanup,waitFor,within} from '@testing-library/react';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import App from './App';
import {eventTarget} from './pages/LiveMonitor';
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
vi.mock('cytoscape',()=>({default:()=>({layout:()=>({run:vi.fn()}),on:vi.fn(),resize:vi.fn(),destroy:vi.fn(),fit:vi.fn(),zoom:vi.fn(()=>1)})}));
globalThis.ResizeObserver=class{observe(){}disconnect(){}unobserve(){}};

const metrics={generated:12,rejected:0,accepted:12,analyzed:11,fully_analyzed:8,failed:1,pending:3,awaiting_immediate:1,awaiting_enrichment:2,new_findings:2,cases_created:1,cases_updated:1,events:90,arrival_rate_per_second:1,throughput_per_minute:40,latest_fully_analyzed_at:'2026-10-08T18:00:00Z',oldest_pending_age_seconds:4,max_backlog:5,ingestion_latency_ms:{mean:8,p95:12},immediate_latency_s:{mean:.2,p95:.5},end_to_end_latency_s:{mean:9,p95:15,max:18},enrichment_runs:1,micro_batch_seconds:{mean:3.1,max:3.1,last:3.1},worker_errors:1,elapsed_seconds:12};
const session=(status:string,extra={})=>({session_id:'STREAM-0003',session_number:3,status,config:{pack_version:'live-stream-pack-v1',scenario_mode:'mixed_demo'},seed:20261010,intended_count:60,generated_count:12,stop_requested:false,cohort:{},metrics,last_event_id:5,last_event:'x',runner:{alive:true},...extra});
const events=[
 {event_id:1,session_id:'STREAM-0003',event_type:'claim_persisted',message:'#1 SC0003-0001 committed to PostgreSQL',claim_id:'SC0003-0001',finding_id:null,case_id:null,provider_id:'PS0003A',stage:'persistence',payload:{},created_at:'2026-10-08T18:00:00Z'},
 {event_id:2,session_id:'STREAM-0003',event_type:'finding_created',message:'rules finding · duplicate billing · HIGH',claim_id:'SC0003-0005',finding_id:'F-1',case_id:null,provider_id:'PS0003A',stage:null,payload:{engine:'rules',finding_type:'duplicate_billing'},created_at:'2026-10-08T18:00:05Z'},
 {event_id:3,session_id:'STREAM-0003',event_type:'case_created',message:'New case CASE-STREAM created from live-stream findings.',claim_id:'SC0003-0005',finding_id:null,case_id:'CASE-STREAM',provider_id:'PS0003A',stage:null,payload:{},created_at:'2026-10-08T18:00:05Z'},
 {event_id:4,session_id:'STREAM-0003',event_type:'processing_failed',message:'Isolation Forest inference failed: model missing',claim_id:null,finding_id:null,case_id:null,provider_id:null,stage:'isolation_forest',payload:{},created_at:'2026-10-08T18:00:06Z'},
];
const stage=(s:string,status:string,extra={})=>({stage:s,label:s.replace('_',' '),layer:'immediate',status,attempts:1,completed_at:status==='COMPLETED'?'2026-10-08T18:00:01Z':null,findings_count:0,reason:null,error:null,result:{},...extra});
const coverage=(full:boolean)=>({claim_id:'SC0003-0001',session_id:'STREAM-0003',sequence:1,scenario:'normal',scenario_label:'Ordinary office visit',scenario_note:'Demo scenario label; no detection engine reads it.',service_date:'2026-09-01',analysis_status:full?'FULLY_ANALYZED':'IMMEDIATE_DONE',fully_analyzed:full,stages:[stage('claim_rules','COMPLETED',{reason:'0 finding(s) from 7 rules'}),stage('supplytrace','NOT_APPLICABLE',{reason:'No itemized supply items'}),stage('member_radar',full?'COMPLETED':'PENDING'),stage('isolation_forest',full?'INSUFFICIENT_DATA':'FAILED',{error:full?null:'model missing'})],finding_ids:[],case_ids:[],enrichment_run:null});

function setup(opts:{status?:string;full?:boolean;startError?:string;path?:string}={}){
 const calls:{url:string;method:string;body?:any}[]=[];
 vi.stubGlobal('fetch',vi.fn(async(url:string,init?:RequestInit)=>{const method=init?.method||'GET';calls.push({url,method,body:init?.body?JSON.parse(String(init.body)):undefined});
  const reply=(data:any,ok=true)=>({ok,json:async()=>data,statusText:'x'});
  if(url.includes('/stream/sessions')&&method==='POST'&&!url.includes('/stop'))return opts.startError?reply({detail:opts.startError},false):reply(session('STARTING'));
  if(url.includes('/stop'))return reply({session_id:'STREAM-0003',status:'STOPPING',acknowledged:true});
  if(url.endsWith('/api/v1/stream'))return reply({session:session(opts.status||'RUNNING'),runner_alive:true,defaults:{pack_version:'live-stream-pack-v1',seed:20261010}});
  if(url.includes('/stream/events'))return reply({items:url.includes('after=0')?events:[],cursor:url.includes('after=0')?4:Number(url.split('after=')[1].split('&')[0]),more:false});
  if(url.includes('/coverage'))return reply(coverage(!!opts.full));
  if(url.includes('/stream/sessions/STREAM-0003/claims'))return reply({items:[{claim_id:'SC0003-0001',sequence:1,scenario_label:'Ordinary office visit',analysis_status:opts.full?'FULLY_ANALYZED':'IMMEDIATE_DONE'}],total:1});
  if(url.includes('/stream/sessions'))return reply({items:[{session_id:'STREAM-0003',status:'RUNNING',generated_count:12,intended_count:60}]});
  if(url.includes('/dataset/summary'))return reply({counts:{claims:21163,providers:267,facilities:60},claims_processed:21151,active_findings:12,active_cases:4,potential_exposure:500,date_range:['2024-10-01','2026-09-30'],trend:[],priority_distribution:[]});
  if(url.includes('/models/status'))return reply({items:[],groq:{configured:false}});
  if(url.includes('/cases/CASE-STREAM'))return reply({case_id:'CASE-STREAM',title:'PS0003A · duplicate billing',primary_entity:'PS0003A',case_status:'NEW',severity:'HIGH',priority_score:70,potential_financial_exposure:500,payment_workflow:'PREPAYMENT',summary:'s',claims:[],findings:[],evidence:[],ranking:{factors:{}},allowed_transitions:['UNDER_REVIEW'],timeline:[]});
  if(url.includes('/claims/SC0003-0001'))return reply({claim:{claim_id:'SC0003-0001',provider_id:'PS0003A',service_date:'2026-09-01',claim_status:'pending',primary_code:'SIM-VISIT',complexity_band:'low',service_duration_min:30,billed_amount_usd:120,allowed_amount_usd:80,paid_amount_usd:0,member_responsibility_usd:0},provider:{provider_name:'Stream Demo Provider 0003A'},facility:{facility_name:'Clinic'},claim_lines:[],supply_items:[],claim_estimates:[],encounter:{},findings:[],cases:[],stream:{session_id:'STREAM-0003',sequence:1,analysis_status:'FULLY_ANALYZED',ingested_at:'2026-10-08T18:00:00Z'}});
  return reply({items:[],total:0});}));
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[opts.path||'/']}><App/></MemoryRouter></QueryClientProvider>);
 return calls;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});

describe('Live Claims Monitor',()=>{
 it('shows server-side session metrics without conflating counts, and keeps the dashboard',async()=>{setup();const monitor=await screen.findByLabelText('Live Claims Monitor');fireEvent.click(await screen.findByRole('button',{name:'Show live monitor details'}));
  expect(within(monitor).getByText('Live Claims Monitor')).toBeInTheDocument();expect(within(monitor).getByText('Watch synthetic healthcare claims move through ClaimShield Nexus in real time.')).toBeInTheDocument();
  await within(monitor).findByText('Fully analyzed');
  const value=(label:string)=>within(monitor).getByText(label).parentElement!.querySelector('strong')!.textContent;
  expect([value('Generated'),value('Accepted'),value('Analyzed'),value('Fully analyzed'),value('Pending'),value('Failed')]).toEqual(['12','12','11','8','3','1']);
  expect(await screen.findByText('21,163')).toBeInTheDocument();expect(screen.getByRole('heading',{name:'Dashboard'})).toBeInTheDocument();});
 it('Start calls the backend with the fixed demo configuration',async()=>{const calls=setup({status:'COMPLETED'});const start=await screen.findByRole('button',{name:/Start Live Simulation/});await waitFor(()=>expect(start).not.toBeDisabled());
  fireEvent.click(start);
  await waitFor(()=>expect(calls.some(c=>c.method==='POST'&&c.url.endsWith('/stream/sessions'))).toBe(true));
  expect(calls.find(c=>c.method==='POST'&&c.url.endsWith('/stream/sessions'))!.body).toEqual({rate_per_second:1,max_claims:90,scenario_mode:'mixed_demo'});});
 it('Stop calls the backend; Start is disabled while a session is active',async()=>{const calls=setup();const stop=await screen.findByRole('button',{name:/Stop Simulation/});await waitFor(()=>expect(stop).not.toBeDisabled());
  expect(screen.getByRole('button',{name:/Start Live Simulation/})).toBeDisabled();fireEvent.click(stop);
  await waitFor(()=>expect(calls.some(c=>c.method==='POST'&&c.url.endsWith('/stream/sessions/STREAM-0003/stop'))).toBe(true));});
 it('shows start errors from the API',async()=>{setup({status:'COMPLETED',startError:'Stream session STREAM-0002 is already active'});const start=await screen.findByRole('button',{name:/Start Live Simulation/});await waitFor(()=>expect(start).not.toBeDisabled());fireEvent.click(start);
  expect(await screen.findByRole('alert')).toHaveTextContent('already active');});
 it('feed shows genuine API events, filters failures and navigates to the claim',async()=>{setup();fireEvent.click(await screen.findByRole('button',{name:'Show live monitor details'}));fireEvent.click(await screen.findByRole('button',{name:/Live events/}));const feed=await screen.findByLabelText('Live event feed');expect(await within(feed).findByText(/SC0003-0001 committed to PostgreSQL/)).toBeInTheDocument();
  expect(within(feed).getByText(/model missing/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Failures'}));await waitFor(()=>expect(within(feed).queryByText(/committed to PostgreSQL/)).toBeNull());fireEvent.click(screen.getByRole('button',{name:'All'}));
  fireEvent.click(await within(feed).findByText(/SC0003-0001 committed to PostgreSQL/));expect(await screen.findByRole('heading',{name:'SC0003-0001'})).toBeInTheDocument();expect(await screen.findByText(/Live-stream synthetic claim/)).toBeInTheDocument();});
 it('case events open the real Case Workspace',async()=>{setup();fireEvent.click(await screen.findByRole('button',{name:'Show live monitor details'}));fireEvent.click(await screen.findByRole('button',{name:/Live events/}));const feed=await screen.findByLabelText('Live event feed');fireEvent.click(await within(feed).findByText(/New case CASE-STREAM created/));expect(await screen.findByText('Investigator controls')).toBeInTheDocument();});
 it('inspector shows pending and failed stages and no Fully analyzed badge until the backend reports it',async()=>{setup();fireEvent.click(await screen.findByRole('button',{name:'Show live monitor details'}));fireEvent.click(await screen.findByRole('button',{name:/Live events/}));fireEvent.click(await screen.findByRole('button',{name:'Inspect SC0003-0001'}));const inspector=await screen.findByLabelText('Pipeline inspector');
  expect(await within(inspector).findByText('model missing')).toBeInTheDocument();expect(within(inspector).getByText('pending')).toBeInTheDocument();expect(within(inspector).queryByText('Fully analyzed')).toBeNull();});
 it('inspector shows Fully analyzed only when reported, and completion is displayed',async()=>{setup({status:'COMPLETED',full:true});fireEvent.click(await screen.findByRole('button',{name:'Show live monitor details'}));fireEvent.click(await screen.findByRole('button',{name:/Recent claims/}));fireEvent.click(await screen.findByRole('button',{name:/^#1 ?SC0003-0001/}));const inspector=await screen.findByLabelText('Pipeline inspector');
  expect(await within(inspector).findByText('Fully analyzed')).toBeInTheDocument();expect(within(inspector).getByText('insufficient data')).toBeInTheDocument();
  expect(within(screen.getByLabelText('Live Claims Monitor')).getAllByText('completed').length).toBeGreaterThan(0);});
 it('routes provider analysis events to radar, network and forecast views',()=>{const base={event_id:9,session_id:'S',message:'',claim_id:null,finding_id:null,case_id:null,stage:null,created_at:'',provider_id:'PS0003C'};
  expect(eventTarget({...base,event_type:'member_radar_completed',payload:{radar_candidate:true}})).toBe('/radar/PS0003C');
  expect(eventTarget({...base,event_type:'member_radar_completed',payload:{radar_candidate:false}})).toBe('/network?entity=PS0003C');
  expect(eventTarget({...base,event_type:'phoenix_completed',payload:{}})).toBe('/network?entity=PS0003C');
  expect(eventTarget({...base,event_type:'forecast_completed',payload:{}})).toBe('/forecast?provider=PS0003C');
  expect(eventTarget({...base,event_type:'finding_created',claim_id:'SC1',payload:{engine:'supplytrace'}})).toBe('/supplytrace/SC1');});
});
