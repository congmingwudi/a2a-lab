/**
 * A2ALab: Data 360 Agentic Memory for an Agentforce agent (plan/18 Phase 2,
 * ADR D83). Run DETERMINISTICALLY from the agent's before_reasoning hook, once
 * per turn — never offered to the planner, so memory does not depend on the
 * model choosing to call it.
 *
 * Wire path: before_reasoning -> this invocable -> Named Credential
 * A2ALab_Bridge -> POST /memory/turn. The bridge holds the memory ECA identity
 * (JWT-bearer + the a360 CDP exchange) and calls the Data 360 tenant; Apex
 * never sees a tenant token, and the tenant leg is traced like every other hop.
 *
 * One call does the whole turn: start the conversation on the first turn
 * (conversationId empty in, the new id out), return the context to read, and
 * ingest the user's message. agentResponse is an optional seam for the
 * agent's REPLY, and nothing feeds it today: no Agent Script variable holds
 * the reply text (@system_variables.last_reply is voice barge-in state, D89),
 * so the concierge binds only the user side. A call with only agentResponse
 * ingests and reads nothing.
 *
 * Fail-open: any failure returns an empty context and ok=false, so a memory
 * outage costs the agent its memory, not the user's turn.
 */
public with sharing class A2ALabMemoryTurn {

    @TestVisible private static final String DEFAULT_AGENT = 'A2ALab_Memory_Concierge';
    // Small on purpose: this runs before EVERY turn, ahead of the planner,
    // inside the same Agentforce action budget (~85-90s, plan/03-results.md).
    private static final Integer TIMEOUT_MS = 20000;

    public class Request {
        @InvocableVariable(label='User Message'
            description='This turn\'s user text (@system_variables.user_input)')
        public String userMessage;

        @InvocableVariable(label='Agent Response'
            description='The agent\'s reply, when a caller has it (D89)')
        public String agentResponse;

        @InvocableVariable(required=true label='Owner Id'
            description='Whose memory this is: the conversation owner id')
        public String ownerId;

        @InvocableVariable(label='Conversation Id'
            description='Memory conversation id from an earlier turn; empty on the first turn')
        public String conversationId;

        @InvocableVariable(label='Agent API Name'
            description='The agent registered for memory; defaults to the concierge')
        public String agentApiName;
    }

    public class Response {
        @InvocableVariable(label='Conversation Id')
        public String conversationId;

        @InvocableVariable(label='Context')
        public String context;

        @InvocableVariable(label='OK')
        public Boolean ok;

        @InvocableVariable(label='Trace Id')
        public String traceId;
    }

    @InvocableMethod(
        label='A2ALab: Memory Turn'
        description='Reads Data 360 Agentic Memory context for this turn and ingests the user message and the agent reply, via the lab bridge.'
        callout=true
    )
    public static List<Response> turn(List<Request> requests) {
        List<Response> out = new List<Response>();
        for (Request req : requests) {
            out.add(turnOne(req));
        }
        return out;
    }

    private static Response turnOne(Request req) {
        Response resp = new Response();
        resp.traceId = EncodingUtil.convertToHex(Crypto.generateAesKey(128));
        resp.conversationId = req.conversationId;
        resp.context = '';
        resp.ok = false;

        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('callout:A2ALab_Bridge/memory/turn');
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        httpReq.setHeader('X-Trace-Id', resp.traceId);
        httpReq.setTimeout(TIMEOUT_MS);
        httpReq.setBody(JSON.serialize(new Map<String, Object>{
            'agent_id' => String.isBlank(req.agentApiName) ? DEFAULT_AGENT : req.agentApiName,
            'owner_id' => req.ownerId,
            'conversation_id' => req.conversationId,
            'user_message' => req.userMessage,
            'agent_response' => req.agentResponse
        }));

        try {
            HttpResponse httpResp = new Http().send(httpReq);
            if (httpResp.getStatusCode() < 200 || httpResp.getStatusCode() >= 300) {
                return resp;
            }
            Map<String, Object> parsed =
                (Map<String, Object>) JSON.deserializeUntyped(httpResp.getBody());
            String cid = (String) parsed.get('conversation_id');
            if (String.isNotBlank(cid)) { resp.conversationId = cid; }
            String ctx = (String) parsed.get('context');
            resp.context = ctx == null ? '' : ctx;
            resp.ok = parsed.get('ok') == true;
        } catch (Exception e) {
            // CalloutException or a non-JSON body: fail open (see class doc).
            resp.ok = false;
        }
        return resp;
    }
}