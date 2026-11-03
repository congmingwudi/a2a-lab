/**
 * Creates CRM follow-up Cases for exact Account names. This is the write-side
 * Agentforce action used by the customer-memory recall-and-act experiment.
 * All validation is per request; SOQL and DML are bulkified and run in user
 * mode so the Einstein Agent User's permissions remain the security boundary.
 */
public with sharing class A2ALabCreateFollowUpCase {
    private static final Set<String> ALLOWED_PRIORITIES =
        new Set<String>{ 'Low', 'Medium', 'High', 'Critical' };

    public class Request {
        @InvocableVariable(
            label='Account Name'
            description='Exact CRM Account name that the new Case belongs to'
            required=true
        )
        public String accountName;

        @InvocableVariable(
            label='Subject'
            description='Concise subject for the follow-up Case (255 characters maximum)'
            required=true
        )
        public String subject;

        @InvocableVariable(
            label='Details'
            description='Symptoms, endpoints, attempted changes, impact, owners, and runbook link'
            required=true
        )
        public String details;

        @InvocableVariable(
            label='Priority'
            description='Low, Medium, High, or Critical; defaults to Medium when omitted'
        )
        public String priority;
    }

    public class Response {
        @InvocableVariable(label='Success' description='Whether exactly one Case was created')
        public Boolean isSuccess;

        @InvocableVariable(label='Case Number' description='Human-readable number of the created Case')
        public String caseNumber;

        @InvocableVariable(label='Case Id' description='Salesforce record id of the created Case')
        public String caseId;

        @InvocableVariable(label='Error Message' description='Safe explanation when no Case was created')
        public String errorMessage;

        @InvocableVariable(label='Error Type' description='Stable diagnostic category for a failed request')
        public String errorType;
    }

    @InvocableMethod(
        label='A2ALab: Create Follow-Up Case'
        description='Creates one CRM Case on an exact Account after an explicit user request'
    )
    public static List<Response> execute(List<Request> requests) {
        if (requests == null || requests.isEmpty()) {
            return new List<Response>();
        }

        List<Response> responses = new List<Response>();
        Set<String> requestedNames = new Set<String>();
        List<String> normalizedPriorities = new List<String>();
        for (Request req : requests) {
            Response res = new Response();
            res.isSuccess = false;
            responses.add(res);
            normalizedPriorities.add(validate(req, res));
            if (res.errorType == null) {
                requestedNames.add(req.accountName.trim());
            }
        }

        Map<String, List<Account>> accountsByName = new Map<String, List<Account>>();
        if (!requestedNames.isEmpty()) {
            for (Account account : [
                SELECT Id, Name
                FROM Account
                WHERE Name IN :requestedNames
                WITH USER_MODE
            ]) {
                String key = account.Name.toLowerCase();
                if (!accountsByName.containsKey(key)) {
                    accountsByName.put(key, new List<Account>());
                }
                accountsByName.get(key).add(account);
            }
        }

        List<Case> pendingCases = new List<Case>();
        List<Integer> pendingIndexes = new List<Integer>();
        for (Integer i = 0; i < requests.size(); i++) {
            Response res = responses[i];
            if (res.errorType != null) {
                continue;
            }
            Request req = requests[i];
            List<Account> matches = accountsByName.get(req.accountName.trim().toLowerCase());
            if (matches == null || matches.isEmpty()) {
                fail(res, 'NOT_FOUND', 'No Account found with the exact name "' +
                    safe(req.accountName, 120) + '". No Case was created.');
                continue;
            }
            if (matches.size() > 1) {
                fail(res, 'AMBIGUOUS_ACCOUNT', 'More than one Account has the exact name "' +
                    safe(req.accountName, 120) + '". No Case was created.');
                continue;
            }
            pendingCases.add(new Case(
                AccountId = matches[0].Id,
                Subject = req.subject.trim(),
                Description = req.details.trim(),
                Priority = normalizedPriorities[i],
                Status = 'New',
                Origin = 'Web'
            ));
            pendingIndexes.add(i);
        }

        if (pendingCases.isEmpty()) {
            return responses;
        }

        Database.SaveResult[] saveResults;
        try {
            saveResults = Database.insert(pendingCases, false, AccessLevel.USER_MODE);
        } catch (Exception e) {
            for (Integer index : pendingIndexes) {
                fail(responses[index], 'DML_ERROR', safe(e.getMessage(), 240));
            }
            return responses;
        }

        Set<Id> createdIds = new Set<Id>();
        Map<Id, Integer> responseIndexById = new Map<Id, Integer>();
        for (Integer i = 0; i < saveResults.size(); i++) {
            Integer responseIndex = pendingIndexes[i];
            Database.SaveResult saved = saveResults[i];
            if (saved.isSuccess()) {
                createdIds.add(saved.getId());
                responseIndexById.put(saved.getId(), responseIndex);
                Response res = responses[responseIndex];
                res.isSuccess = true;
                res.caseId = String.valueOf(saved.getId());
            } else {
                Database.Error err = saved.getErrors()[0];
                fail(responses[responseIndex], 'DML_ERROR', safe(err.getMessage(), 240));
            }
        }

        if (!createdIds.isEmpty()) {
            for (Case created : new CreatedCaseNumberReader().read(createdIds)) {
                Response res = responses[responseIndexById.get(created.Id)];
                res.caseNumber = created.CaseNumber;
                res.errorMessage = null;
                res.errorType = null;
            }
        }
        return responses;
    }

    /**
     * A Case routing automation can move a newly inserted Case to a queue before
     * this action rereads it. The action's user-mode insert remains the write
     * authorization boundary; this helper exposes only the generated CaseNumber
     * for the exact SaveResult ids created by that authorized operation.
     */
    private without sharing class CreatedCaseNumberReader {
        @SuppressWarnings('PMD.ApexCRUDViolation')
        private List<Case> read(Set<Id> createdIds) {
            return [
                SELECT Id, CaseNumber
                FROM Case
                WHERE Id IN :createdIds
            ];
        }
    }

    private static String validate(Request req, Response res) {
        if (req == null || String.isBlank(req.accountName)) {
            fail(res, 'VALIDATION_ERROR', 'Account name is required. No Case was created.');
        } else if (String.isBlank(req.subject)) {
            fail(res, 'VALIDATION_ERROR', 'Case subject is required. No Case was created.');
        } else if (req.subject.trim().length() > 255) {
            fail(res, 'VALIDATION_ERROR', 'Case subject must be 255 characters or fewer.');
        } else if (String.isBlank(req.details)) {
            fail(res, 'VALIDATION_ERROR', 'Case details are required. No Case was created.');
        } else if (req.details.trim().length() > 32000) {
            fail(res, 'VALIDATION_ERROR', 'Case details must be 32000 characters or fewer.');
        }

        String priority = String.isBlank(req == null ? null : req.priority)
            ? 'Medium' : req.priority.trim();
        if (res.errorType == null && !ALLOWED_PRIORITIES.contains(priority)) {
            fail(res, 'VALIDATION_ERROR', 'Priority must be Low, Medium, High, or Critical.');
        }
        return priority;
    }

    private static void fail(Response res, String errorType, String message) {
        res.isSuccess = false;
        res.errorType = errorType;
        res.errorMessage = message;
    }

    private static String safe(String value, Integer maxLength) {
        String cleaned = String.isBlank(value) ? 'Unknown error' : value.replaceAll('[\\r\\n]+', ' ').trim();
        return cleaned.length() <= maxLength ? cleaned : cleaned.substring(0, maxLength);
    }
}
