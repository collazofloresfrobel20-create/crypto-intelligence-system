import assert from "node:assert/strict";
import test from "node:test";
import {DatabaseSync} from "node:sqlite";
import {aggregatePortfolio,getPortfolio,portfolioOptions,simulatePosition} from "../src/portfolio.js";

const hour=3600000, t=Date.parse("2026-11-02T00:00:00Z"), due=t+7*24*hour;
const stamp=n=>new Date(n).toISOString();
const options={days:7,stake:100};
const pred={id:1,created_at:stamp(t),price_at_prediction:10,token_key:"bsc:a",chain:"bsc"};
const bar=(start,op=10,hi=10,lo=10,end=10)=>[start,op,hi,lo,end,start+hour-1];
const point=(time,price)=>({observed_at:stamp(time),price});

test("Peak excludes prices after expiry, costs occur on both sides, and gaps can exceed a stop",()=>{
  const bars=Array.from({length:169},(_,i)=>bar(t+i*hour));
  bars[1]=bar(t+hour,8,10,8,9);
  bars[20]=bar(t+20*hour,10,13,10,11);
  bars[168]=bar(due,11,60,10,12);
  const {strategies:s}=simulatePosition(pred,bars,[],options,due+2*hour);
  assert.equal(s.hold.value_usd,119.28);
  assert.equal(s.peak.exit_price,13);
  assert.equal(s.peak.interval_start,stamp(t+20*hour));
  assert.equal(s.stop15.exit_price,8);
  assert.equal(s.stop15.pnl_usd,-20.48);
});

test("Intrabar ordering, partial entry candles and expiry straddles are explicit",()=>{
  const bars=[bar(t,10,14,8,11)];
  const p=simulatePosition(pred,bars,[],options,t+2*hour);
  assert.equal(p.strategies.tp30_sl15.status,"ambiguous");
  assert.equal(p.strategies.stop15.status,"settled");
  assert.equal(p.strategies.hold.status,"pending");
  assert.equal(simulatePosition({...pred,created_at:stamp(t+hour/2)},bars,[],options,t+2*hour).strategies.stop15.status,"ambiguous");
  const endBars=Array.from({length:170},(_,i)=>bar(t+i*hour));
  endBars[168]=bar(due,10,20,6,10);
  const result=simulatePosition({...pred,created_at:stamp(t+hour/2)},endBars,[],options,due+3*hour);
  assert.equal(result.strategies.stop15.status,"ambiguous");
  assert.equal(result.strategies.peak.exit_price,10);
});

test("Partial paths do not invent an early exit or a final basket value",()=>{
  const q=[point(t+12*hour,11),point(due,9)];
  const p=simulatePosition(pred,[],q,options,due+2*hour);
  assert.equal(p.strategies.hold.status,"settled");
  assert.equal(p.strategies.stop20.status,"missing");
  const lost=simulatePosition(pred,[],q,options,due+24*hour);
  assert.equal(lost.strategies.peak.status,"missing");
  const noClose=simulatePosition(pred,[],[],options,due+24*hour);
  const summary=aggregatePortfolio([p,noClose],100).find(r=>r.key==="hold");
  assert.equal(summary.allocated_usd,200);
  assert.equal(summary.settled_capital_usd,100);
  assert.equal(summary.final_value_usd,null);
  assert.equal(aggregatePortfolio([],100)[0].final_value_usd,null);
});

test("DEX ticks cannot imply an undisclosed fill at the stop threshold",()=>{
  const q=[point(t+12*hour,7)];
  const {strategies}=simulatePosition(pred,[],q,options,t+24*hour);
  assert.equal(strategies.stop20.exit_price,7);
  assert.equal(strategies.tp40_sl20.status,"settled");
  assert.equal(strategies.peak.status,"pending");
});

test("Weekly API excludes future rows, separates identities, filters verdict before deduplication",async()=>{
  const sql=new DatabaseSync(":memory:");
  sql.exec(`CREATE TABLE predictions(id INTEGER,category,symbol,name,chain_name,verdict,contract_address,created_at,price_at_prediction,source,current_price,price_checked_at,price_after,evaluated_at);
    CREATE TABLE portfolio_paths(prediction_id,bars); CREATE TABLE portfolio_quotes(token_key,observed_at,price);
    CREATE TABLE survivor_observations(run_id,token_key,observed_at,price,source);
    CREATE TABLE shadow_captures(run_id,status); CREATE TABLE horizon_prices(token_key,observed_at,price);`);
  const add=sql.prepare("INSERT INTO predictions(id,category,symbol,name,chain_name,verdict,contract_address,created_at,price_at_prediction) VALUES(?,'analyzed','X','X',?,?,?,?,10)");
  add.run(1,"BNB Chain","Watchlist","0xAA",stamp(t));
  add.run(2,"BNB Chain","Strong Opportunity","0xaa",stamp(t+hour));
  add.run(3,"BNB","Strong Opportunity","0xAA",stamp(t+2*hour));
  add.run(4,"Solana","Strong Opportunity","aB",stamp(t+hour));
  add.run(5,"Solana","Strong Opportunity","Ab",stamp(t+hour));
  add.run(6,"Solana","Strong Opportunity",null,stamp(t+hour));
  add.run(7,"Solana","Strong Opportunity","X",stamp(t+30*24*hour));
  const db={all:(q,args)=>sql.prepare(q).all(...args)};
  const r=await getPortfolio(db,new URL("https://test?cohort=strong"),t+3*hour);
  assert.equal(r.week,"2026-11-02");assert.equal(r.weeks.length,1);assert.equal(r.excluded_identity,1);
  assert.deepEqual(r.positions.map(p=>p.id),[2,4,5]);
  assert.deepEqual((await getPortfolio(db,new URL("https://test?chain=bsc&cohort=all"),t+3*hour)).positions.map(p=>p.id),[1]);
  assert.throws(()=>portfolioOptions(new URL("https://test?week=2026-02-30")),RangeError);
  assert.throws(()=>portfolioOptions(new URL("https://test?stake=-1")),RangeError);
  assert.throws(()=>portfolioOptions(new URL("https://test?days=0")),RangeError);
  sql.close();
});
