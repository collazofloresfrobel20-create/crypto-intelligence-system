import test from "node:test";
import assert from "node:assert/strict";
import {DatabaseSync} from "node:sqlite";
import {readFileSync} from "node:fs";
import {tokenSearch} from "../src/token-search.js";

const bsc="0x"+"AB".repeat(20), sol="So"+"1".repeat(35);
const key="bsc:"+bsc.toLowerCase(), other="solana:"+sol;
function fixture(){
  const sql=new DatabaseSync(":memory:");
  const schema=readFileSync(new URL("../../backend/app/db.py",import.meta.url),"utf8").split('SCHEMA = """')[1].split('"""')[0];
  sql.exec(schema);
  const insert=sql.prepare("INSERT INTO predictions (run_id,symbol,name,chain_name,contract_address,category,created_at,status,return_pct) VALUES('r','OLD','Old brand','BNB',?,'analyzed',?,?,?)");
  insert.run(bsc,"2026-11-01","evaluated",-17);insert.run(bsc,"2026-11-20","pending",null);
  sql.prepare("INSERT INTO search_archive VALUES (?,0,'2026-11-20','b','AAA','Alias Actual',?,'2026-11-01',1,0.8)").run(key,'["Liquidez"]');
  sql.prepare("INSERT INTO survivor_observations(run_id,token_key,observed_at,symbol,eligible,shortlisted) VALUES('r',?,'2026-11-10','OLD',1,1)").run(key);
  sql.prepare("INSERT INTO research_dossiers VALUES ('r',?,'2026-11-10','{}')").run(key);
  return {all:async(query,args)=>sql.prepare(query).all(...args)};
}
const remote=async()=>Response.json({pairs:[
  {chainId:"solana",priceUsd:"1",baseToken:{symbol:"AAA",name:"Other",address:sol}},
  {chainId:"bsc",priceUsd:"2",baseToken:{symbol:"OTHER",address:bsc},quoteToken:{symbol:"AAA"}},
  {chainId:"ethereum",priceUsd:"3",baseToken:{symbol:"AAA",address:bsc}},
]});
async function search(db,args,f=remote){
  const response=await tokenSearch(db,new URL("https://cis.test/api/v2/search?"+new URLSearchParams(args)),f);
  return {status:response.status,...await response.json()};
}
test("alias changes, old return and rejected history stay bound to their exact chain and address",async()=>{
  const result=await search(fixture(),{q:"AAA"});
  assert.deepEqual(result.items.map(x=>x.key),[key,other]);
  assert.equal(result.items[0].analyzed.status,"pending");
  assert.equal(result.items[0].closed.pct,-17);
  assert.deepEqual(result.items[0].rejection.reasons,["Liquidez"]);
  assert.equal(result.items[0].dossier.run,"r");
  assert.equal(result.items[1].analyzed,undefined);
});
test("case-sensitive Solana keys, network validation and failed provider",async()=>{
  const db=fixture(), unavailable=async()=>{throw Error();};
  const exact=await search(db,{q:"solana:"+sol},unavailable);
  assert.equal(exact.items[0].key,other);
  assert.equal(exact.market_status,"no_disponible");
  assert.equal((await search(db,{q:bsc,chain:"solana"})).status,400);
  const aliases=await search(db,{q:"alias actual"},unavailable);
  assert.equal(aliases.items[0].analyzed.id,2);
  assert.equal((await search(db,{q:"",chain:"bsc"})).items[0].key,key);
});
