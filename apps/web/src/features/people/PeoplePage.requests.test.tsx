import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { BrowserRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ScopeProvider } from "../../app/ScopeProvider";
import { PeoplePage } from "./PeoplePage";
import type { FamilyPerson, FamilyRelationship } from "../../shared/api/client";

const p = (id: string, display_name: string, changes: Partial<FamilyPerson> = {}): FamilyPerson => ({
  id, display_name, status:"confirmed", scope_id:"album1", membership:null, portrait:null,
  avatar_face_instance_id:null, appearance_count:5, representative_media:[], aliases:[], hidden:false,
  ...changes
});
const rel = (s: string, o: string, predicate: string, inverse_predicate: string, changes: Partial<FamilyRelationship> = {}) => ({
  id:s+o, scope_id:"album1", subject_entity_id:s, object_entity_id:o, predicate, inverse_predicate,
  locked:true, source:"user_override", state:"effective", ...changes
});
const pair = (a: string, b: string, changes: Partial<FamilyRelationship> = {}) => [rel(a,b,"父亲","儿子",changes),rel(b,a,"儿子","父亲",changes)];
const a = p("a","老周"), b = p("b","小周"), c = p("c","小王");
const pending = p("pending","", {status:"pending"});
const capture: { path: string; method: string; payload: Record<string, unknown> | null }[] = [];
const json = (data: unknown, status=200) => new Response(JSON.stringify(data), {status});
const writes = () => capture.filter((x)=>x.path.startsWith("/api/v1/") && x.method!=="GET");
interface Options {
  people?: FamilyPerson[];
  rows?: ReturnType<typeof pair>;
  person?: string | null;
  afterRename?: string[];
  override?: (request: (typeof capture)[number]) => Response | Promise<Response> | undefined;
}
function mount({ people = [a,b,c], rows = pair("a","b"), person = "a", afterRename = ["老周"], override }: Options = {}) {
  window.history.replaceState(null,"","/people"+(person ? "?person="+person : ""));
  let currentPeople = people;
  vi.stubGlobal("fetch", vi.fn(async (address: string, req?: RequestInit) => {
    const url = new URL(address,window.location.href);
    const entry = {path:url.pathname,method:req?.method ?? "GET",payload:typeof req?.body === "string" ? JSON.parse(req.body as string) : null};
    capture.push(entry);
    const {path,payload} = entry;
    const custom = override?.(entry);
    if (custom !== undefined) return custom;
    if (path.endsWith("/family-graph")) return json({people:currentPeople,relationships:rows,scope_ids:["album1","album2"],run:null});
    if (path.endsWith("/portrait")) return json({portrait_text:"示例画像",feedback:null});
    if (path.endsWith("/name")) {
      const id = path.split("/").at(-2);
      currentPeople = currentPeople.map((person) => person.id === id
        ? { ...person, display_name: payload.name, aliases: afterRename }
        : person);
      return json({entity:{canonical_name:payload.name},aliases:afterRename});
    }
    if (path.endsWith("/batch-confirm")) return json({confirmed:[{entity_id:payload.items[0].person_id}],failed:[],count:1});
    if (path.endsWith("/merge")) {
      const peopleInMerge = people.filter((item) => item.id === payload.target_person_id || payload.source_person_ids.includes(item.id));
      return json({entity:{id:payload.target_person_id,status:peopleInMerge.some((item) => item.status === "confirmed") ? "confirmed" : "pending",
        canonical_name:payload.name, aliases:[]}});
    }
    if (/\/people\/[^/]+$/.test(path)) return json({entity:{aliases:[]},clusters:[]});
    return json({ok:true,relationships:[],retracted:[],sync_status:"synced",sync_error:""});
  }));
  return render(<BrowserRouter><ScopeProvider><PeoplePage /></ScopeProvider></BrowserRouter>);
}
const button=(name: string, area: Pick<typeof screen, "getByRole"> = screen) => area.getByRole<HTMLButtonElement>("button",{name});
const detail=()=>screen.findByRole("complementary");
const fields=(role: string,reverse?: string)=>{fireEvent.change(screen.getByLabelText("关系称谓"),{target:{value:role}});if(reverse!==undefined)fireEvent.change(screen.getByLabelText("反向称谓"),{target:{value:reverse}});};
const pick=(id: string)=>fireEvent.click(document.querySelector('[data-option-id="'+id+'"]')!);
const openGraph=()=>fireEvent.click(screen.getByRole("tab",{name:/^关系/}));
const editEdge=(container: HTMLElement,id="a->b")=>fireEvent.click(container.querySelector('[data-edge-id="'+id+'"]')!);
const drag=(container: HTMLElement,from: string,to: string)=>{
  const coords=(id: string)=>{
    const view=container.querySelector(".graph-svg > g")!.getAttribute("transform") ?? "";
    const [, tx, ty, k]=/translate\(([-\d.e]+) ([-\d.e]+)\) scale\(([-\d.e]+)\)/.exec(view)!;
    const node=container.querySelector('[data-node-id="'+id+'"]')!.getAttribute("transform") ?? "";
    const [, x, y]=/translate\(([-\d.e]+) ([-\d.e]+)\)/.exec(node)!;
    return {clientX:+tx + +k * +x,clientY:+ty + +k * +y};
  };
  fireEvent.pointerDown(container.querySelector('[data-relate-handle="'+from+'"]')!,{...coords(from),pointerId:1,button:0});
  fireEvent.pointerMove(window,{...coords(to),pointerId:1});
  fireEvent.pointerUp(window,{...coords(to),pointerId:1});
};
const put = (s: string,o: string,pred: string,inverse: string,scope_id="album1") => ({
  path:"/api/v1/family-graph/relationships",method:"PUT",
  payload:{scope_id,subject_entity_id:s,predicate:pred,object_entity_id:o,inverse_predicate:inverse}
});
afterEach(()=>{ cleanup(); vi.unstubAllGlobals(); capture.length=0; });

it("1 same name pauses; separate sends flag false; no merge",async()=>{
  mount({people:[a,b,pending],person:"pending"}); await detail();
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"  老周  "}});
  fireEvent.click(button("确认身份"));
  await screen.findByRole("dialog"); expect(writes()).toEqual([]);
  fireEvent.click(button("不是，只是同名"));
  expect(writes()).toEqual([{path:"/api/v1/people/batch-confirm",method:"POST",payload:{items:[{person_id:"pending",name:"老周",family_role:null,merge_same_name:false}]}}]);
});

it("1 cancelling sends zero writes; confirmation without a visible namesake still disallows merging",async()=>{
  mount({people:[a,b,pending],person:"pending"}); await detail();
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"老周"}});
  fireEvent.click(button("确认身份")); await screen.findByRole("dialog");
  fireEvent.click(button("取消",within(screen.getByRole("dialog"))));
  expect(writes()).toEqual([]);
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"  小新  "}});
  fireEvent.change(screen.getByLabelText("家庭角色"),{target:{value:"父亲"}});
  fireEvent.click(button("确认身份"));
  expect(writes()).toEqual([{path:"/api/v1/people/batch-confirm",method:"POST",payload:{items:[{person_id:"pending",name:"小新",family_role:"父亲",merge_same_name:false}]}}]);
});

it("2 repeated graph drop writes the latest target",async()=>{
  const {container}=mount({person:null,rows:[...pair("a","b"),...pair("b","c")]});
  await screen.findByText("老周"); openGraph();
  drag(container,"a","b"); await detail();
  expect(document.querySelector('[data-option-id="b"]')!.getAttribute("aria-pressed")).toBe("true");
  drag(container,"a","c");
  expect(document.querySelector('[data-option-id="c"]')!.getAttribute("aria-pressed")).toBe("true");
  fields("同事"); fireEvent.click(button("建立关系"));
  expect(writes()).toEqual([put("c","a","同事","同事")]);
});

it("3 both confirmed and inferred existing edges require a clearly labeled replacement",async()=>{
  mount(); await detail(); pick("b"); fields("朋友");
  expect(screen.getByText(/将替换现有关系/)).toBeTruthy();
  fireEvent.click(button("替换关系"));
  expect(writes()).toEqual([put("b","a","朋友","朋友")]);
  cleanup(); capture.length=0;
  mount({rows:pair("a","b",{source:"model",locked:false})});
  await detail(); pick("b"); fields("朋友");
  expect(screen.getByText(/将替换现有关系/).textContent).toContain("系统推断");
  fireEvent.click(button("替换关系"));
  expect(writes()).toEqual([put("b","a","朋友","朋友")]);
});

it("4 alias draft preserves the old name added by a rename",async()=>{
  mount({people:[p("a","老周",{aliases:["阿周"]}),b,c],afterRename:["阿周","老周"]});
  await detail(); fireEvent.click(button("编辑别名"));
  fireEvent.change(screen.getByLabelText("人物别名"),{target:{value:"阿周、周哥"}});
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"  周老师  "}});
  fireEvent.click(button("保存姓名"));
  await waitFor(()=>expect(button("保存别名").disabled).toBe(false));
  fireEvent.click(button("保存别名"));
  expect(writes()).toEqual([
    {path:"/api/v1/people/a/name",method:"PATCH",payload:{name:"周老师"}},
    {path:"/api/v1/entities/a/properties/aliases",method:"PUT",payload:{value:["阿周","周哥","老周"]}}
  ]);
});

it("5 correct relation in both directions; detail DELETE has real scope and IDs",async()=>{
  mount({person:"b"}); await detail();
  expect(document.querySelector(".relation-sentence")!.textContent).toBe("老周 是 小周 的父亲");
  fireEvent.click(button("删除关系：老周 是 小周 的父亲"));
  expect(writes()).toEqual([{path:"/api/v1/family-graph/relationships",method:"DELETE",payload:{scope_id:"album1",subject_entity_id:"a",object_entity_id:"b"}}]);
  cleanup(); capture.length=0;
  mount(); await detail();
  expect(document.querySelector(".relation-sentence")!.textContent).toBe("小周 是 老周 的儿子");
});

it("6 switching back to A cannot send another in-flight feedback",async()=>{
  const {container}=mount({override:(e)=>e.path.endsWith("/portrait-feedback") ? new Promise<Response>(()=>{}) : undefined});
  await detail(); await screen.findByText("示例画像");
  expect(screen.queryByRole("tab",{name:"人物介绍"})).toBeNull();
  fireEvent.click(button("像他")); expect(button("不像他").disabled).toBe(true);
  fireEvent.click(container.querySelector('[data-person-id="b"]')!);
  await screen.findByRole("complementary",{name:"小周 的详情"}); await screen.findByText("示例画像");
  fireEvent.click(button("像他"));
  fireEvent.click(container.querySelector('[data-person-id="a"]')!);
  await screen.findByRole("complementary",{name:"老周 的详情"}); await screen.findByText("示例画像");
  expect(button("不像他").disabled).toBe(true);
  fireEvent.click(button("不像他"));
  expect(writes().map((x)=>[x.path,x.payload?.feedback])).toEqual([
    ["/api/v1/people/a/portrait-feedback","像他"],
    ["/api/v1/people/b/portrait-feedback","像他"],
  ]);
});

it("7 empty and whitespace confirmed name blocked via click, submit and keyboard",async()=>{
  mount(); await detail(); const name=screen.getByLabelText("姓名");
  for(const value of ["","   ","  老周  "]){
    fireEvent.change(name,{target:{value}}); expect(button("保存姓名").disabled).toBe(true);
    fireEvent.click(button("保存姓名"));fireEvent.keyDown(name,{key:"Enter"});fireEvent.submit(name.closest("form")!);
  }
  expect(writes()).toEqual([]);
});

it("merge into a confirmed person explains that the proposed role only applies if kept apart",async()=>{
  mount({people:[a,b,pending],person:"pending"}); await detail();
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"老周"}});
  fireEvent.change(screen.getByLabelText("家庭角色"),{target:{value:"父亲"}});
  fireEvent.click(button("确认身份")); await screen.findByRole("dialog");
  expect(screen.getByText(/「父亲」只在分开确认时保存/)).toBeTruthy();
  fireEvent.click(button("是同一个人，合并")); await screen.findByRole("complementary",{name:"老周 的详情"});
  expect(writes()).toEqual([{path:"/api/v1/people/merge",method:"POST",payload:{target_person_id:"a",source_person_ids:["pending"],name:"老周"}}]);
});

it("merge of two pending people confirms only the person retained by the API",async()=>{
  const existing=p("a","老周",{status:"pending"});
  mount({people:[existing,b,pending,c],person:"pending",override:(entry)=>
    entry.path.endsWith("/merge") ? json({entity:{id:"a",status:"pending",canonical_name:"老周",aliases:[]}}) : undefined});
  await detail(); fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"老周"}});
  fireEvent.click(button("确认身份")); await screen.findByRole("dialog");
  fireEvent.click(button("是同一个人，合并")); await screen.findByRole("complementary",{name:"老周 的详情"});
  expect(writes()).toEqual([
    {path:"/api/v1/people/merge",method:"POST",payload:{target_person_id:"a",source_person_ids:["pending"],name:"老周"}},
    {path:"/api/v1/people/batch-confirm",method:"POST",payload:{items:[{person_id:"a",name:"老周",family_role:null,merge_same_name:false}]}}
  ]);
});

it("failed follow-up after a successful merge retries confirmation for the retained person only", async () => {
  const existing = p("a","老周",{status:"pending"});
  let first = true;
  mount({people:[existing,b,pending],person:"pending",override:(entry) => {
    if (!entry.path.endsWith("/batch-confirm") || !first) return undefined;
    first = false;
    return json({confirmed:[],failed:[{error:"模拟确认失败"}],count:0});
  }});
  await detail();
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"老周"}});
  fireEvent.change(screen.getByLabelText("家庭角色"),{target:{value:"父亲"}});
  fireEvent.click(button("确认身份"));
  await screen.findByRole("dialog");
  fireEvent.click(button("是同一个人，合并"));
  await screen.findByText(/人物已合并，身份待确认/);
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(button("重试确认身份"));
  expect(writes()).toEqual([
    {path:"/api/v1/people/merge",method:"POST",payload:{target_person_id:"a",source_person_ids:["pending"],name:"老周"}},
    ...[1,2].map(() => ({path:"/api/v1/people/batch-confirm",method:"POST",payload:{items:[{person_id:"a",name:"老周",family_role:"父亲",merge_same_name:false}]}})),
  ]);
});

it("lets the user select the correct person from all same-name candidates", async () => {
  const matches = [p("a","老周",{appearance_count:8}),p("b","老周",{appearance_count:2}),pending];
  mount({people:matches,person:"pending"});
  await detail();
  fireEvent.change(screen.getByLabelText("姓名"),{target:{value:"老周"}});
  fireEvent.click(button("确认身份"));
  const dialog = within(await screen.findByRole("dialog"));
  expect(dialog.getAllByRole("radio")).toHaveLength(2);
  expect(button("是同一个人，合并", dialog).disabled).toBe(true);
  fireEvent.click(dialog.getByRole("radio",{name:/出现 2 次/}));
  fireEvent.click(button("是同一个人，合并", dialog));
  expect(writes()).toEqual([{path:"/api/v1/people/merge",method:"POST",payload:{target_person_id:"b",source_person_ids:["pending"],name:"老周"}}]);
});

it("treats inherited JavaScript property names as ordinary custom relationship labels", async () => {
  mount({rows:[]});
  await detail();
  pick("b");
  for (const label of ["constructor","hasOwnProperty","__proto__"]) {
    fields(label);
    expect(screen.getByLabelText<HTMLInputElement>("反向称谓").value).toBe(label);
  }
  fireEvent.click(button("建立关系"));
  expect(writes()).toEqual([put("b","a","__proto__","__proto__")]);
});

it("validates the same Unicode character categories and length as the relationship API", async () => {
  mount({rows:[]});
  await detail();
  pick("b");
  fields("同\u200b事","同事");
  expect(button("建立关系").disabled).toBe(true);
  fireEvent.submit(button("建立关系").closest("form")!);
  expect(writes()).toEqual([]);
  fields("𠮷".repeat(17),"朋友");
  expect(button("建立关系").disabled).toBe(true);
  const label = "𠮷".repeat(16);
  fields(label,"朋友");
  expect(button("建立关系").disabled).toBe(false);
  fireEvent.click(button("建立关系"));
  expect(writes()).toEqual([put("b","a",label,"朋友")]);
});

it("graph editing and deleting send correct HTTP contract and non-default scope",async()=>{
  const people=[p("a","老周",{scope_id:"album2"}),p("b","小周",{scope_id:"album2"}),c];
  const {container}=mount({people,person:null,rows:pair("a","b",{scope_id:"album2"})});
  await screen.findByText("老周"); openGraph(); editEdge(container);
  let editor=screen.getByRole("region",{name:"编辑关系"});
  fields("同事","前同事"); fireEvent.click(button("保存关系",within(editor)));
  fireEvent.click(await screen.findByRole("button",{name:"完成"}));
  editEdge(container); editor=screen.getByRole("region",{name:"编辑关系"});
  fireEvent.click(button("删除关系",within(editor)));
  expect(writes()).toEqual([
    put("a","b","同事","前同事","album2"),
    {path:"/api/v1/family-graph/relationships",method:"DELETE",payload:{scope_id:"album2",subject_entity_id:"a",object_entity_id:"b"}}
  ]);
});
it("locks both graph and detail while the same relationship write is pending", async () => {
  const {container} = mount({override: (entry) =>
    entry.method === "PUT" && entry.path.endsWith("/relationships") ? new Promise<Response>(() => {}) : undefined});
  await detail(); openGraph(); editEdge(container);
  const editor = within(screen.getByRole("region",{name:"编辑关系"}));
  fireEvent.change(editor.getByLabelText("关系称谓"),{target:{value:"朋友"}});
  fireEvent.click(button("保存关系",editor));
  const remove = button("删除关系：小周 是 老周 的儿子");
  expect(remove.disabled).toBe(true);
  fireEvent.click(remove);
  expect(writes()).toEqual([put("a","b","朋友","朋友")]);
});

it("ignores a graph drop over someone in another album", async () => {
  const {container} = mount({people:[a,b,p("c","小王",{scope_id:"album2"})],person:null});
  await screen.findByText("老周");
  openGraph();
  drag(container,"a","c");
  expect(screen.queryByRole("complementary")).toBeNull();
  expect(writes()).toEqual([]);
});

// ---- 写回执先落本地；记忆同步在后台（2026-11-19） ------------------------------------

/** 第一次图谱读取照常返回，之后的重拉一直挂着：断言界面不靠重拉也能反应。 */
const holdRefetch = (rest?: Options["override"]): Options["override"] => {
  let reads = 0;
  return (request) => {
    if (request.path.endsWith("/family-graph") && ++reads > 1) return new Promise<Response>(() => {});
    return rest?.(request);
  };
};

it("4 a saved relationship shows from the PUT receipt while the graph refetch is still out, without locking identity edits",async()=>{
  let release: (response: Response) => void = () => {};
  let checks = 0;
  mount({rows:[],override:holdRefetch((request)=>{
    if (request.method==="PUT") return new Promise<Response>((resolve)=>{ release=resolve; });
    if (request.path.endsWith("/jobs/job1")) return json({id:"job1",status:++checks > 1 ? "synced" : "pending",error:""});
    return undefined;
  })});
  await detail(); pick("b"); fields("同事");
  fireEvent.click(button("建立关系"));
  // 关系在保存中：只锁这一对人，身份段照常可编辑。
  expect(button("建立关系").disabled).toBe(true);
  expect(button("编辑别名").disabled).toBe(false);
  expect((screen.getByLabelText("姓名") as HTMLInputElement).disabled).toBe(false);

  release(json({ok:true,relationships:[rel("b","a","同事","同事"),rel("a","b","同事","同事")],
    sync_status:"pending",sync_error:"",sync_job_id:"job1"}));
  const sentence=(text: string)=>(_: string, element: Element | null)=>
    element?.className==="relation-sentence" && element.textContent===text;
  expect(await screen.findByText(sentence("小周 是 老周 的同事"))).toBeTruthy();
  expect(await screen.findByText("修改已保存，正在同步关联记忆。")).toBeTruthy();
  expect(await screen.findByText("修改已保存，关联记忆已同步。",{},{timeout:3000})).toBeTruthy();
});

it("4 deleting an edge from the graph removes it from the receipt and reports background sync",async()=>{
  const {container}=mount({person:null,override:holdRefetch((request)=>{
    if (request.method==="DELETE") return json({ok:true,retracted:pair("a","b",{state:"retracted"}),
      sync_status:"pending",sync_error:"",sync_job_id:"job2"});
    if (request.path.endsWith("/jobs/job2")) return json({id:"job2",status:"pending",error:""});
    return undefined;
  })});
  await screen.findByText("老周"); openGraph();
  editEdge(container); fireEvent.click(button("删除关系"));
  expect(await screen.findByText("关系已保存，正在后台同步图谱与检索索引。")).toBeTruthy();
  expect(container.querySelector('[data-edge-id="a->b"]')).toBeNull();
  expect(screen.getByText(/0 条已确认关系/)).toBeTruthy();
});

it("4 changing membership updates the roster from the receipt",async()=>{
  mount({override:holdRefetch((request)=>{
    if (request.path.endsWith("/membership")) return json({ok:true,membership:{id:"m",scope_id:"album1",entity_id:"a",
      membership:"family",source:"user_override",confidence:1,evidence_refs:[],inference_run_id:null,state:"effective",
      locked:true,revision:1,supersedes_id:null,created_at:"",updated_at:""},sync_status:"pending",sync_error:"",sync_job_id:"job3"});
    if (request.path.endsWith("/jobs/job3")) return json({id:"job3",status:"pending",error:""});
    return undefined;
  })});
  await detail();
  fireEvent.change(screen.getByRole("combobox",{name:/家庭归属/}),{target:{value:"family"}});
  await waitFor(()=>expect((screen.getByRole("combobox",{name:/家庭归属/}) as HTMLSelectElement).value).toBe("family"));
});
