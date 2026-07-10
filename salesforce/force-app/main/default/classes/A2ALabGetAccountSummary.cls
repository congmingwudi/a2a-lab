/**
 * A2ALab: agent action that returns a short current-state summary of a
 * customer account from real CRM records — Account + open Opportunities +
 * open Cases. Exposed to the A2ALab Research Assistant via the
 * A2ALab_Get_Account_Summary GenAiFunction so account answers are grounded
 * in SOQL results, not prompt-embedded data.
 *
 * without sharing: the action executes as the agent's bot user, which sits
 * outside the role hierarchy and owns no records — with sharing it can read
 * the Account but sees zero Opportunities/Cases. The action is read-only
 * and returns only a summary string.
 */
public without sharing class A2ALabGetAccountSummary {

    public class Request {
        @InvocableVariable(required=true label='Account Name'
            description='Name (or partial name) of the account to summarize')
        public String accountName;
    }

    public class Response {
        @InvocableVariable(label='Summary'
            description='Short current-state summary: tenure, open opportunities, open cases, health')
        public String summary;
    }

    @InvocableMethod(
        label='A2ALab: Get Account Summary'
        description='Looks up a customer account by name and returns a short summary of its current state: relationship notes, open opportunities with amounts and stages, and open support cases with priorities.')
    public static List<Response> run(List<Request> requests) {
        List<Response> out = new List<Response>();
        for (Request req : requests) {
            Response res = new Response();
            res.summary = summarize(req.accountName);
            out.add(res);
        }
        return out;
    }

    private static String summarize(String accountName) {
        String needle = '%' + String.escapeSingleQuotes(accountName == null ? '' : accountName.trim()) + '%';
        List<Account> accounts = [
            SELECT Id, Name, Industry, Description
            FROM Account
            WHERE Name LIKE :needle
            ORDER BY Name
            LIMIT 1
        ];
        if (accounts.isEmpty()) {
            return 'No account found matching "' + accountName + '".';
        }
        Account acct = accounts[0];

        List<String> parts = new List<String>();
        String head = acct.Name;
        if (String.isNotBlank(acct.Industry)) head += ' (' + acct.Industry + ')';
        parts.add(head + '.');
        if (String.isNotBlank(acct.Description)) parts.add(acct.Description);

        List<Opportunity> opps = [
            SELECT Name, Amount, StageName
            FROM Opportunity
            WHERE AccountId = :acct.Id AND IsClosed = false
            ORDER BY Amount DESC NULLS LAST
        ];
        if (opps.isEmpty()) {
            parts.add('No open opportunities.');
        } else {
            List<String> oppBits = new List<String>();
            for (Opportunity opp : opps) {
                String bit = opp.Name;
                if (opp.Amount != null) bit += ' (~$' + String.valueOf(opp.Amount.longValue()) + ')';
                bit += ' at stage ' + opp.StageName;
                oppBits.add(bit);
            }
            parts.add('Open opportunities: ' + String.join(oppBits, '; ') + '.');
        }

        List<Case> cases = [
            SELECT Subject, Priority
            FROM Case
            WHERE AccountId = :acct.Id AND IsClosed = false
            ORDER BY Priority
        ];
        if (cases.isEmpty()) {
            parts.add('No open support cases.');
        } else {
            List<String> caseBits = new List<String>();
            for (Case c : cases) {
                caseBits.add((String.isBlank(c.Priority) ? 'Unprioritized' : c.Priority) +
                    ': ' + (String.isBlank(c.Subject) ? '(no subject)' : c.Subject));
            }
            parts.add(cases.size() + ' open support case' + (cases.size() == 1 ? '' : 's') +
                ' — ' + String.join(caseBits, '; ') + '.');
        }

        return String.join(parts, ' ');
    }
}
