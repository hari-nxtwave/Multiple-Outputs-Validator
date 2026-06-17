import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        int[] arr = new int[n];
        for (int i = 0; i < n; i++) arr[i] = sc.nextInt();

        Solution sol = new Solution();
        List<Integer> result = sol.findLargestChain(arr);   // the user's returned output

        // ---- Independently compute the canonical largest divisible subset ---- //
        int[] sorted = arr.clone();
        Arrays.sort(sorted);
        int[] length = new int[n];
        int[] prev = new int[n];
        Arrays.fill(length, 1);
        Arrays.fill(prev, -1);
        int maxLen = 0, maxIdx = -1;
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < i; j++) {
                if (sorted[i] % sorted[j] == 0 && length[j] + 1 > length[i]) {
                    length[i] = length[j] + 1;
                    prev[i] = j;
                }
            }
            if (length[i] > maxLen) { maxLen = length[i]; maxIdx = i; }
        }
        List<Integer> canonical = new ArrayList<>();
        for (int i = maxIdx; i >= 0; i = prev[i]) canonical.add(sorted[i]);
        Collections.sort(canonical);   // deterministic canonical form

        // ---- Validate the user's returned output against ALL constraints ----- //
        boolean valid = isValid(result, arr, maxLen);

        // ---- Emit canonical if valid (matches stored expected), else raw ------ //
        StringBuilder sb = new StringBuilder();
        List<Integer> toPrint = valid ? canonical : safe(result);
        for (int i = 0; i < toPrint.size(); i++) {
            if (i > 0) sb.append(' ');
            sb.append(toPrint.get(i));
        }
        System.out.println(sb.toString());
    }

    /** A returned subset is valid iff: it uses only input elements (no duplicates),
     *  every pair is mutually divisible, and its size equals the true maximum. */
    static boolean isValid(List<Integer> result, int[] arr, int maxLen) {
        if (result == null) return false;
        // 1) optimal size
        if (result.size() != maxLen) return false;
        // 2) all elements drawn from the input, each at most once (distinct input)
        Map<Integer, Integer> avail = new HashMap<>();
        for (int x : arr) avail.merge(x, 1, Integer::sum);
        for (int v : result) {
            Integer left = avail.get(v);
            if (left == null || left == 0) return false;
            avail.put(v, left - 1);
        }
        // 3) every pair mutually divisible
        for (int i = 0; i < result.size(); i++) {
            for (int j = i + 1; j < result.size(); j++) {
                int a = result.get(i), b = result.get(j);
                if (a == 0 || b == 0) return false;
                if (a % b != 0 && b % a != 0) return false;
            }
        }
        return true;
    }

    static List<Integer> safe(List<Integer> r) {
        return r == null ? new ArrayList<>() : r;
    }
}
