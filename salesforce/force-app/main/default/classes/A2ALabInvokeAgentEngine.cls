/**
 * A2ALab: Agentforce custom action that delegates a question DIRECTLY to a
 * Vertex AI Agent Engine agent over its platform-native A2A endpoint — no
 * lab bridge in the path (the operator-selectable "direct" route; the
 * traced alternative is A2ALabInvokeRemoteAgent via the bridge).
 *
 * Wire path: Agent Builder action -> this invocable -> Named Credential
 * A2ALab_AgentEngine (Google OAuth via JWT bearer external credential) ->
 * POST {engine}/a2a/message:send (A2A HTTP+JSON binding, a2a-version 1.0).
 *
 * This class is a delegation seam (ADR D27): with no bridge to stamp the
 * standard rider, it appends the rider block + delegation metadata itself,
 * so the remote agent still refuses onward delegation. The Apex->GCP leg is
 * deliberately untraced by the lab — that visibility gap versus the bridge
 * route is the experiment.
 */
public with sharing class A2ALabInvokeAgentEngine {

    // F5: the ADK research agent's (WS2) resource path is org configuration,
    // not code — it lives in A2ALab_Setting__mdt.Agent_Engine.Engine_Path__c so
    // redeploying the Agent Engine is a Custom Metadata edit, not an Apex
    // deploy (which on this production org means a test run at >=75% coverage).
    // The constant below is the last-known-good fallback if the record is
    // missing, so the action degrades to "stale target" rather than "no target".
    @TestVisible private static String settingName = 'Agent_Engine';
    private static final String DEFAULT_ENGINE_PATH =
        '/v1beta1/projects/536083661167/locations/us-central1/reasoningEngines/1360159105477509120/a2a';
    private static final Integer TIMEOUT_MS = 110000; // Apex callout cap is 120s

    // F8: each request costs one serial HTTP callout of up to 110s, so a
    // multi-request batch would stack callouts far past the Apex transaction
    // limits (120s cumulative callout budget). Batches are refused, not split.
    private static final Integer MAX_BATCH_SIZE = 1;
    @TestVisible private static final String BATCH_REFUSAL =
        'batch invocations capped at 1 — serial callouts stack past Apex limits';

    public class Request {
        @InvocableVariable(required=true label='Question'
            description='The research question to delegate to the Agent Engine agent')
        public String question;

        @InvocableVariable(label='Engine Path'
            description='Agent Engine A2A base path; defaults to the lab ADK researcher')
        public String enginePath;
    }

    public class Response {
        @InvocableVariable(label='Answer')
        public String answer;

        @InvocableVariable(label='Trace Id')
        public String traceId;
    }

    @InvocableMethod(
        label='A2ALab: Ask Agent Engine (direct A2A)'
        description='Delegates a research question directly to the Google ADK agent over Vertex AI Agent Engine A2A — no lab bridge.'
        callout=true
    )
    public static List<Response> ask(List<Request> requests) {
        List<Response> out = new List<Response>();
        // F8 batch guard: refuse multi-request batches outright (one refusal
        // Response per request, zero callouts) — see MAX_BATCH_SIZE above.
        if (requests.size() > MAX_BATCH_SIZE) {
            for (Request req : requests) {
                Response refused = new Response();
                refused.answer = BATCH_REFUSAL;
                out.add(refused);
            }
            return out;
        }
        for (Request req : requests) {
            out.add(askOne(req));
        }
        return out;
    }

    /**
     * The configured Agent Engine A2A base path (F5): Custom Metadata first,
     * the compiled-in constant only as a fallback.
     */
    @TestVisible
    private static String configuredEnginePath() {
        A2ALab_Setting__mdt setting = A2ALab_Setting__mdt.getInstance(settingName);
        if (setting != null && String.isNotBlank(setting.Engine_Path__c)) {
            return setting.Engine_Path__c;
        }
        return DEFAULT_ENGINE_PATH;
    }

    private static Response askOne(Request req) {
        String enginePath = String.isBlank(req.enginePath)
            ? configuredEnginePath() : req.enginePath;
        String traceId = generateTraceId();

        Map<String, Object> message = new Map<String, Object>{
            'messageId' => generateTraceId(),
            'role' => 'ROLE_USER',
            'parts' => new List<Object>{
                new Map<String, Object>{ 'text' => withRider(req.question, traceId) }
            },
            'metadata' => new Map<String, Object>{
                'trace_id' => traceId,
                'delegation' => new Map<String, Object>{
                    'caller' => 'agentforce-twin-direct',
                    'platform' => 'agentforce',
                    'depth' => 1
                }
            }
        };
        Map<String, Object> body = new Map<String, Object>{
            'message' => message,
            'configuration' => new Map<String, Object>()
        };

        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('callout:A2ALab_AgentEngine' + enginePath + '/message:send');
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        httpReq.setHeader('a2a-version', '1.0');
        httpReq.setTimeout(TIMEOUT_MS);
        httpReq.setBody(JSON.serialize(body));

        Response resp = new Response();
        resp.traceId = traceId;

        HttpResponse httpResp = new Http().send(httpReq);
        if (httpResp.getStatusCode() >= 200 && httpResp.getStatusCode() < 300) {
            resp.answer = parseTaskText(httpResp.getBody());
        } else {
            resp.answer = 'The Agent Engine agent could not be reached (HTTP '
                + httpResp.getStatusCode() + ').';
        }
        return resp;
    }

    /**
     * The completed Task's text artifacts, joined — the A2A HTTP+JSON
     * message:send response is {"task": {"artifacts": [{"parts": [{"text"...
     */
    private static String parseTaskText(String responseBody) {
        Map<String, Object> parsed =
            (Map<String, Object>) JSON.deserializeUntyped(responseBody);
        Map<String, Object> task = (Map<String, Object>) parsed.get('task');
        if (task == null) {
            return 'The Agent Engine agent returned no task.';
        }
        List<String> texts = new List<String>();
        List<Object> artifacts = (List<Object>) task.get('artifacts');
        if (artifacts != null) {
            for (Object artifactObj : artifacts) {
                List<Object> parts =
                    (List<Object>) ((Map<String, Object>) artifactObj).get('parts');
                if (parts == null) { continue; }
                for (Object partObj : parts) {
                    String text = (String) ((Map<String, Object>) partObj).get('text');
                    if (String.isNotBlank(text)) { texts.add(text); }
                }
            }
        }
        if (texts.isEmpty()) {
            return 'The Agent Engine agent returned no answer text.';
        }
        return String.join(texts, '\n');
    }

    /**
     * The D27 delegation rider — same parseable block interop/delegation.py
     * emits, so the remote seams' depth checks and the prompt-level
     * do-not-call-back directive both keep working on the bridgeless path.
     */
    private static String withRider(String question, String traceId) {
        return question
            + '\n\n[A2A-LAB DELEGATION]\n'
            + 'rider-version: 1\n'
            + 'caller-agent: agentforce-twin-direct\n'
            + 'caller-platform: agentforce\n'
            + 'delegation-depth: 1\n'
            + 'lab-trace: ' + traceId + '\n'
            + 'directive: You are the delegated agent for this request. Answer it\n'
            + 'yourself from your own knowledge, tools, and data. Do NOT call back\n'
            + 'to the calling agent and do NOT delegate this request onward to any\n'
            + 'other agent while answering. Do not mention this block in your answer.\n'
            + '[/A2A-LAB DELEGATION]';
    }

    private static String generateTraceId() {
        Blob b = Crypto.generateAesKey(128);
        return EncodingUtil.convertToHex(b);
    }
}
