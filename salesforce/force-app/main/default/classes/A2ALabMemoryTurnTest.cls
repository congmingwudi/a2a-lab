/**
 * Tests for A2ALabMemoryTurn. Production deploys require running tests with
 * >=75% coverage; covers the first turn (new conversation id), a later turn
 * (id kept), the bridge's own fail-open (ok=false), a non-2xx, a non-JSON
 * body and a callout exception — the last three must all fail open.
 */
@IsTest
private class A2ALabMemoryTurnTest {

    private class BridgeMock implements HttpCalloutMock {
        Integer statusCode;
        String body;
        Boolean throwIt = false;
        HttpRequest lastRequest;

        BridgeMock(Integer statusCode, String body) {
            this.statusCode = statusCode;
            this.body = body;
        }

        public HttpResponse respond(HttpRequest req) {
            this.lastRequest = req;
            if (throwIt) { throw new CalloutException('connection reset'); }
            HttpResponse res = new HttpResponse();
            res.setStatusCode(this.statusCode);
            res.setBody(this.body);
            return res;
        }
    }

    private static A2ALabMemoryTurn.Response run(String conversationId) {
        A2ALabMemoryTurn.Request req = new A2ALabMemoryTurn.Request();
        req.userMessage = 'I prefer aisle seats';
        req.ownerId = 'persona-ana';
        req.conversationId = conversationId;
        List<A2ALabMemoryTurn.Response> out =
            A2ALabMemoryTurn.turn(new List<A2ALabMemoryTurn.Request>{ req });
        System.assertEquals(1, out.size());
        return out[0];
    }

    @IsTest
    static void firstTurnReturnsTheNewConversationAndContext() {
        BridgeMock mock = new BridgeMock(200,
            '{"conversation_id": "a2alab-c1", "context": "- summary: Lisbon", "ok": true}');
        Test.setMock(HttpCalloutMock.class, mock);
        Test.startTest();
        A2ALabMemoryTurn.Response r = run(null);
        Test.stopTest();

        System.assertEquals('a2alab-c1', r.conversationId);
        System.assertEquals('- summary: Lisbon', r.context);
        System.assert(r.ok);
        System.assert(mock.lastRequest.getEndpoint().endsWith('/memory/turn'),
            mock.lastRequest.getEndpoint());
        Map<String, Object> sent =
            (Map<String, Object>) JSON.deserializeUntyped(mock.lastRequest.getBody());
        System.assertEquals(A2ALabMemoryTurn.DEFAULT_AGENT, sent.get('agent_id'));
        System.assertEquals('persona-ana', sent.get('owner_id'));
        System.assertEquals('I prefer aisle seats', sent.get('user_message'));
        System.assertEquals(r.traceId, mock.lastRequest.getHeader('X-Trace-Id'));
    }

    @IsTest
    static void replyOnlyCallSendsTheReply() {
        BridgeMock mock = new BridgeMock(200,
            '{"conversation_id": "a2alab-c1", "context": "", "ok": true}');
        Test.setMock(HttpCalloutMock.class, mock);
        A2ALabMemoryTurn.Request req = new A2ALabMemoryTurn.Request();
        req.agentResponse = 'Noted: aisle seats.';
        req.ownerId = 'persona-ana';
        req.conversationId = 'a2alab-c1';
        Test.startTest();
        A2ALabMemoryTurn.Response r =
            A2ALabMemoryTurn.turn(new List<A2ALabMemoryTurn.Request>{ req })[0];
        Test.stopTest();
        System.assert(r.ok);
        Map<String, Object> sent =
            (Map<String, Object>) JSON.deserializeUntyped(mock.lastRequest.getBody());
        System.assertEquals('Noted: aisle seats.', sent.get('agent_response'));
        System.assertEquals(null, sent.get('user_message'));
    }

    @IsTest
    static void laterTurnKeepsItsConversation() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(200,
            '{"conversation_id": "a2alab-c1", "context": null, "ok": true}'));
        Test.startTest();
        A2ALabMemoryTurn.Response r = run('a2alab-c1');
        Test.stopTest();
        System.assertEquals('a2alab-c1', r.conversationId);
        System.assertEquals('', r.context);
    }

    @IsTest
    static void bridgeFailOpenIsReported() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(200,
            '{"conversation_id": "a2alab-c1", "context": "", "ok": false, "error": "403"}'));
        Test.startTest();
        A2ALabMemoryTurn.Response r = run('a2alab-c1');
        Test.stopTest();
        System.assert(!r.ok);
        System.assertEquals('', r.context);
    }

    @IsTest
    static void non2xxFailsOpen() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(401, 'bad token'));
        Test.startTest();
        A2ALabMemoryTurn.Response r = run('a2alab-c1');
        Test.stopTest();
        System.assert(!r.ok);
        System.assertEquals('a2alab-c1', r.conversationId, 'the id in must survive a failure');
    }

    @IsTest
    static void nonJsonBodyFailsOpen() {
        Test.setMock(HttpCalloutMock.class, new BridgeMock(200, '<html>gateway</html>'));
        Test.startTest();
        A2ALabMemoryTurn.Response r = run(null);
        Test.stopTest();
        System.assert(!r.ok);
        System.assertEquals('', r.context);
    }

    @IsTest
    static void calloutExceptionFailsOpen() {
        BridgeMock mock = new BridgeMock(200, '{}');
        mock.throwIt = true;
        Test.setMock(HttpCalloutMock.class, mock);
        Test.startTest();
        A2ALabMemoryTurn.Response r = run(null);
        Test.stopTest();
        System.assert(!r.ok);
    }
}
