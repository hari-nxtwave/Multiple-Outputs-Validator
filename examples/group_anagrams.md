# Group Anagrams

Given an array of strings `strs`, group the anagrams together. You can return the
answer **in any order**. Each group contains words that are anagrams of one
another. Within a group and across groups, the order does not matter.

Implement:

```java
class Solution {
    public List<List<String>> groupAnagrams(String[] strs) { ... }
}
```

Input format: the first line is `n`, followed by `n` whitespace-separated words.
Output: print each group on its own line, words separated by a single space.

```java
import java.util.*;

public class Main {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int n = sc.nextInt();
        String[] strs = new String[n];
        for (int i = 0; i < n; i++) {
            strs[i] = sc.next();
        }
        Solution sol = new Solution();
        List<List<String>> lists = sol.groupAnagrams(strs);
        for (List<String> group : lists) {
            System.out.println(String.join(" ", group));
        }
    }
}
```
