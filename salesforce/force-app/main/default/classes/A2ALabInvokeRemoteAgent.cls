/**
 * A2ALab: Agentforce custom action that delegates a question to a remote
 * agent (Claude/OpenAI) through the lab bridge.
 *
 * Wire path: Agent Builder action -> this invocable -> Named Credential
 * A2ALab_Bridge (X-Bridge-Token header) -> bridge POST /invoke/{target} ->
 * remote agent over rest|mcp|a2a per config/targets.yaml. Switching the
 * remote protocol never requires redeploying this class.
 *
 * The trace id is forwarded as X-Trace-Id so the hop shows up correlated in
 * the lab console.
 */
public with sharing class A2ALabInvokeRemoteAgent {

    private static final String DEFAULT_TARGET = 'claude-rest';
    private static final Integer TIMEOUT_MS = 110000; // Apex callout cap is 120s

    public class Request {
        @InvocableVariable(required=true label='Question'
            description='The research question to delegate to the remote agent')
        public String question;

        @InvocableVariable(label='Session Id'
            description='Conversation id so the remote agent can keep context')
        public String sessionId;

        @InvocableVariable(label='Target'
            description='Bridge target name (see config/targets.yaml); defaults to claude-rest')
        public String target;
    }

    public class Response {
        @InvocableVariable(label='Answer')
        public String answer;

        @InvocableVariable(label='Trace Id')
        public String traceId;
    }

    @InvocableMethod(
        label='A2ALab: Ask Remote Agent'
        description='Delegates a research question to a remote AI agent via the A2A lab bridge and returns its answer.'
        callout=true
    )
    public static List<Response> ask(List<Request> requests) {
        List<Response> out = new List<Response>();
        for (Request req : requests) {
            out.add(askOne(req));
        }
        return out;
    }

    private static Response askOne(Request req) {
        String target = String.isBlank(req.target) ? DEFAULT_TARGET : req.target;
        String traceId = generateTraceId();

        Map<String, Object> body = new Map<String, Object>{
            'message' => req.question,
            'session_id' => req.sessionId,
            'trace_id' => traceId
        };

        HttpRequest httpReq = new HttpRequest();
        httpReq.setEndpoint('callout:A2ALab_Bridge/invoke/' + target);
        httpReq.setMethod('POST');
        httpReq.setHeader('Content-Type', 'application/json');
        httpReq.setHeader('X-Trace-Id', traceId);
        httpReq.setTimeout(TIMEOUT_MS);
        httpReq.setBody(JSON.serialize(body));

        Response resp = new Response();
        resp.traceId = traceId;

        HttpResponse httpResp = new Http().send(httpReq);
        if (httpResp.getStatusCode() >= 200 && httpResp.getStatusCode() < 300) {
            Map<String, Object> parsed =
                (Map<String, Object>) JSON.deserializeUntyped(httpResp.getBody());
            resp.answer = (String) parsed.get('text');
        } else {
            resp.answer = 'The remote agent could not be reached (HTTP '
                + httpResp.getStatusCode() + ').';
        }
        return resp;
    }

    private static String generateTraceId() {
        Blob b = Crypto.generateAesKey(128);
        return EncodingUtil.convertToHex(b);
    }
}
